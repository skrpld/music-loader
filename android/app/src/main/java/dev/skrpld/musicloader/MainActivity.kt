package dev.skrpld.musicloader

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.skrpld.musicloader.data.DownloadMode
import dev.skrpld.musicloader.data.ThemeMode
import dev.skrpld.musicloader.ui.MainViewModel
import dev.skrpld.musicloader.ui.MusicLoaderApp
import dev.skrpld.musicloader.ui.download.DownloadViewModel
import dev.skrpld.musicloader.ui.jobs.JobsViewModel
import dev.skrpld.musicloader.ui.settings.SettingsViewModel
import dev.skrpld.musicloader.ui.supportsDynamicColor
import dev.skrpld.musicloader.ui.theme.MusicLoaderTheme
import dev.skrpld.musicloader.ui.theme.isDark

class MainActivity : ComponentActivity() {
    private val factory get() = (application as MusicLoaderApplication).container.viewModelFactory

    private val mainViewModel: MainViewModel by viewModels { factory }
    private val downloadViewModel: DownloadViewModel by viewModels { factory }
    private val jobsViewModel: JobsViewModel by viewModels { factory }
    private val settingsViewModel: SettingsViewModel by viewModels { factory }

    // The download progress notification of the phone mode (Android 13+).
    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}
    private var notificationPermissionAsked = false

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) handleShare(intent)
        addOnNewIntentListener { handleShare(it) }

        setContent {
            val settings by mainViewModel.settings.collectAsStateWithLifecycle()
            val darkTheme = (settings?.themeMode ?: ThemeMode.System).isDark()
            val phoneMode = settings?.mode == DownloadMode.Phone
            LaunchedEffect(phoneMode) {
                if (phoneMode) askForNotifications()
            }
            // System bar icons follow the app theme, which may differ from the system one.
            DisposableEffect(darkTheme) {
                enableEdgeToEdge(
                    statusBarStyle = SystemBarStyle.auto(Color.TRANSPARENT, Color.TRANSPARENT) { darkTheme },
                    navigationBarStyle = SystemBarStyle.auto(Color.TRANSPARENT, Color.TRANSPARENT) { darkTheme },
                )
                onDispose {}
            }
            MusicLoaderTheme(darkTheme = darkTheme, dynamicColor = settings?.dynamicColor ?: true) {
                MusicLoaderApp(
                    mainViewModel = mainViewModel,
                    downloadViewModel = downloadViewModel,
                    jobsViewModel = jobsViewModel,
                    settingsViewModel = settingsViewModel,
                    appVersion = BuildConfig.VERSION_NAME,
                    dynamicColorAvailable = supportsDynamicColor,
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Back from the storage permission screen: the phone mode may be able to start now.
        mainViewModel.retry()
    }

    private fun askForNotifications() {
        if (notificationPermissionAsked || Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        notificationPermissionAsked = true
        val permission = Manifest.permission.POST_NOTIFICATIONS
        if (ContextCompat.checkSelfPermission(this, permission) != PackageManager.PERMISSION_GRANTED) {
            notificationPermission.launch(permission)
        }
    }

    /** Links shared from Spotify, SoundCloud or a browser land in the download form. */
    private fun handleShare(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND) return
        val text = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return
        downloadViewModel.receiveShared(text)
    }
}
