package dev.skrpld.musicloader.engine

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.MediaScannerConnection
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationChannelCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import dev.skrpld.musicloader.MainActivity
import dev.skrpld.musicloader.MusicLoaderApplication
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.Connection
import dev.skrpld.musicloader.data.Job
import dev.skrpld.musicloader.data.JobStatus
import dev.skrpld.musicloader.data.ServerState
import java.io.File
import java.text.DateFormat
import java.util.Date
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.mapNotNull
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Keeps the app process - and with it the downloader - alive while the phone works
 * off the queue: a foreground service with a progress notification and a partial wake
 * lock. Stops itself once nothing is running or queued. Finished tracks are handed to
 * the media scanner so music players see them.
 *
 * A finished job that waits for its automatic retry (the cooldown after a rate limit) counts
 * as work: the retry timer lives in the downloader inside this process, so the service stays
 * until the retry has run. Killing the app drops the timer.
 */
class DownloadService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var watching = false
    private var wakeLock: PowerManager.WakeLock? = null
    private val scanned = mutableSetOf<String>()
    private val createdAt = System.currentTimeMillis()
    private val timeFormat: DateFormat by lazy { android.text.format.DateFormat.getTimeFormat(this) }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        NotificationManagerCompat.from(this).createNotificationChannel(
            NotificationChannelCompat.Builder(CHANNEL_ID, NotificationManagerCompat.IMPORTANCE_LOW)
                .setName(getString(R.string.notification_channel_downloads))
                .build(),
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            notification(null),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0,
        )
        acquireWakeLock()
        if (!watching) {
            watching = true
            scope.launch { watch() }
        }
        return START_NOT_STICKY
    }

    private suspend fun watch() {
        val container = (application as MusicLoaderApplication).container
        val states = container.repository.connection.mapNotNull { connection ->
            (connection as? Connection.Online)?.takeIf { it.local }?.state
        }
        // The state that is current when the service starts may predate the job that
        // started it; an idle queue only counts once the service has seen it busy or
        // after a grace period.
        var sawBusy = false
        states.collectLatest { state ->
            scanFinished(state, container.settings.settings.first().musicDir.ifBlank { container.localEngine.defaultMusicDir })
            if (state.isBusy) {
                sawBusy = true
                updateNotification(state)
            } else {
                if (!sawBusy) delay(IDLE_GRACE_MILLIS)
                stopSelf()
            }
        }
    }

    @SuppressLint("MissingPermission")
    private fun updateNotification(state: ServerState) {
        val allowed = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        if (allowed) NotificationManagerCompat.from(this).notify(NOTIFICATION_ID, notification(state))
    }

    private val ServerState.isBusy: Boolean
        get() = jobs.any { it.status == JobStatus.Running || it.status == JobStatus.Queued || it.isWaitingForRetry }

    private suspend fun scanFinished(state: ServerState, musicDir: String) {
        // Jobs that finished while the service ran; older ones were scanned back then.
        val finished = state.jobs.filter {
            it.status.isFinished && it.startedAt != null && (it.finishedAt ?: 0L) >= createdAt && it.id !in scanned
        }
        if (finished.isEmpty()) return
        finished.forEach { scanned += it.id }
        val since = finished.minOf { it.startedAt ?: 0L } - SCAN_SLACK_MILLIS
        val paths = withContext(Dispatchers.IO) {
            File(musicDir).walkTopDown()
                .onEnter { !it.name.startsWith(".") }
                .filter { it.isFile && it.extension.lowercase() in SCANNED_EXTENSIONS && it.lastModified() >= since }
                .map { it.absolutePath }
                .toList()
        }
        if (paths.isNotEmpty()) {
            MediaScannerConnection.scanFile(applicationContext, paths.toTypedArray(), null, null)
        }
    }

    private fun notification(state: ServerState?): Notification {
        val job: Job? = state?.active ?: state?.jobs?.firstOrNull { it.status == JobStatus.Running }
        val queued = state?.jobs?.count { it.status == JobStatus.Queued } ?: 0
        val nextRetry = state?.jobs?.mapNotNull { if (it.isWaitingForRetry) it.retryAt else null }?.minOrNull()
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val builder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_download)
            .setContentTitle(getString(R.string.notification_downloading))
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_PROGRESS)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
        val stats = job?.stats
        if (job == null && queued == 0 && nextRetry != null) {
            // Only waiting for the cooldown after a rate limit.
            builder.setContentText(getString(R.string.notification_waiting_retry, timeFormat.format(Date(nextRetry))))
            builder.setProgress(0, 0, false)
        } else if (stats != null && stats.tracksTotal > 0) {
            builder.setContentText(getString(R.string.progress_tracks, stats.tracksProcessed, stats.tracksTotal))
            builder.setProgress(stats.tracksTotal, stats.tracksProcessed, false)
        } else {
            builder.setContentText(getString(R.string.progress_tracks_resolving))
            builder.setProgress(0, 0, true)
        }
        job?.files?.firstOrNull()?.label?.takeIf { it.isNotBlank() }?.let { builder.setSubText(it) }
        if (queued > 0) builder.setSubText(getString(R.string.notification_queued, queued))
        return builder.build()
    }

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        val power = getSystemService(PowerManager::class.java) ?: return
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "MusicLoader:downloads").apply {
            setReferenceCounted(false)
            acquire(WAKE_LOCK_TIMEOUT_MILLIS)
        }
    }

    /** Android 15+ limits data sync services to 6 hours a day. */
    override fun onTimeout(startId: Int, fgsType: Int) {
        stopSelf()
    }

    override fun onDestroy() {
        scope.cancel()
        wakeLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
        super.onDestroy()
    }

    private companion object {
        const val CHANNEL_ID = "downloads"
        const val NOTIFICATION_ID = 1
        const val IDLE_GRACE_MILLIS = 15_000L
        const val SCAN_SLACK_MILLIS = 5_000L
        const val WAKE_LOCK_TIMEOUT_MILLIS = 6 * 60 * 60 * 1000L
        val SCANNED_EXTENSIONS = setOf("mp3", "m4a", "opus", "ogg", "flac", "wav", "m3u8", "m3u")
    }
}
