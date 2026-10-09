package dev.skrpld.musicloader.ui.jobs

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularWavyProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.LinearWavyProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.WavyProgressIndicatorDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.Job
import dev.skrpld.musicloader.data.LyricsMode
import dev.skrpld.musicloader.ui.formatDuration
import kotlinx.coroutines.delay
import kotlin.math.roundToInt

/** Track progress of a running job, or null while the total is still unknown. */
private fun trackFraction(job: Job): Float? {
    val total = job.stats.tracksTotal
    return if (total > 0) (job.stats.tracksProcessed.toFloat() / total).coerceIn(0f, 1f) else null
}

/**
 * Big wavy circle with the percentage, link and track counters next to it. The last line is
 * the track summary, or the elapsed time where the counters are shown in their own block.
 */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun JobProgressHeader(job: Job, modifier: Modifier = Modifier, showElapsed: Boolean = false) {
    val fraction = trackFraction(job)
    val animated by animateFloatAsState(
        targetValue = fraction ?: 0f,
        animationSpec = WavyProgressIndicatorDefaults.ProgressAnimationSpec,
        label = "tracks",
    )
    Row(
        modifier = modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        Box(contentAlignment = Alignment.Center) {
            if (fraction == null) {
                CircularWavyProgressIndicator(modifier = Modifier.size(88.dp))
            } else {
                CircularWavyProgressIndicator(progress = { animated }, modifier = Modifier.size(88.dp))
                Text(
                    text = stringResource(R.string.percent, (animated * 100).roundToInt()),
                    style = MaterialTheme.typography.titleMediumEmphasized,
                )
            }
        }
        Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(
                text = stringResource(R.string.progress_links, job.queue.completed, job.queue.total),
                style = MaterialTheme.typography.bodyLarge,
            )
            Text(
                text = if (job.stats.tracksTotal > 0) {
                    stringResource(R.string.progress_tracks, job.stats.tracksProcessed, job.stats.tracksTotal)
                } else {
                    stringResource(R.string.progress_tracks_resolving)
                },
                style = MaterialTheme.typography.bodyLarge,
            )
            Text(
                text = if (showElapsed) elapsedText(job) else trackSummary(job),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/** Link queue as a wavy bar. */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun LinkQueueProgress(job: Job, modifier: Modifier = Modifier) {
    val total = job.queue.total
    val target = if (total > 0) (job.queue.completed.toFloat() / total).coerceIn(0f, 1f) else 0f
    val animated by animateFloatAsState(
        targetValue = target,
        animationSpec = WavyProgressIndicatorDefaults.ProgressAnimationSpec,
        label = "links",
    )
    LinearWavyProgressIndicator(progress = { animated }, modifier = modifier.fillMaxWidth())
}

/** "Elapsed: 3:21", ticking once a second while the job runs. */
@Composable
private fun elapsedText(job: Job): String {
    val started = job.startedAt ?: return statusLabel(job)
    val now by produceState(System.currentTimeMillis()) {
        while (true) {
            delay(1_000)
            value = System.currentTimeMillis()
        }
    }
    return stringResource(R.string.job_elapsed, formatDuration(now - started))
}

/**
 * One row per download slot. A slot that has been used stays as an idle row, and the speed
 * line is always there, so the block keeps its height for the whole job.
 */
@Composable
fun ActiveDownloads(job: Job, modifier: Modifier = Modifier) {
    val files = job.files.sortedBy { it.slot }
    // Not state: it only grows and is read in the same composition that updates it.
    val slots = remember(job.id) { intArrayOf(1) }
    slots[0] = maxOf(slots[0], files.size)
    // A single track is done before its ETA means anything.
    val showEta = job.stats.tracksTotal > 1
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        files.forEach { file ->
            key(file.slot) {
                val details = listOf(file.speed, if (showEta) file.eta else "")
                    .filter { it.isNotBlank() }
                    .joinToString(" · ")
                DownloadRow(label = file.label, fraction = (file.percent / 100f).coerceIn(0f, 1f), details = details)
            }
        }
        repeat(slots[0] - files.size) {
            DownloadRow(label = stringResource(R.string.progress_slot_idle), fraction = 0f, details = "", idle = true)
        }
    }
}

@Composable
private fun DownloadRow(label: String, fraction: Float, details: String, idle: Boolean = false) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(
            text = label,
            style = MaterialTheme.typography.bodyMedium,
            color = if (idle) muted else Color.Unspecified,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        LinearProgressIndicator(progress = { fraction }, modifier = Modifier.fillMaxWidth())
        Text(
            // A placeholder until the speed is known keeps the row from changing height.
            text = details.ifEmpty { "—" },
            style = MaterialTheme.typography.labelSmall,
            color = muted,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/**
 * Track and lyrics counters in one block, one labelled row per metric. With both services in
 * the job the tracks are also split by service.
 */
@Composable
fun JobStatistics(job: Job, modifier: Modifier = Modifier) {
    val stats = job.stats
    val colors = MaterialTheme.colorScheme
    val spotify = stats.spotifyTracksTotal > 0 || stats.spotifyOk + stats.spotifyFail > 0
    val soundcloud = stats.soundcloudTracksTotal > 0 || stats.soundcloudOk + stats.soundcloudFail > 0
    val split = spotify && soundcloud
    fun cells(spotifyValue: Int?, soundcloudValue: Int?, total: Int): List<String> =
        if (split) listOf(spotifyValue?.toString() ?: "—", soundcloudValue?.toString() ?: "—", total.toString())
        else listOf(total.toString())

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
        colors = CardDefaults.cardColors(containerColor = colors.surfaceContainerLow),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (split) {
                StatRow(
                    label = "",
                    cells = listOf(serviceName("spotify"), serviceName("soundcloud"), stringResource(R.string.stat_total)),
                    header = true,
                )
            }
            StatRow(
                label = stringResource(R.string.stat_downloaded),
                cells = cells(stats.spotifyTracksDone, stats.soundcloudTracksDone, stats.tracksDone),
                accent = colors.primary,
            )
            StatRow(
                label = stringResource(R.string.stat_skipped),
                cells = cells(stats.spotifyTracksSkipped, stats.soundcloudTracksSkipped, stats.tracksSkipped),
                accent = colors.secondary,
            )
            StatRow(
                label = stringResource(R.string.stat_failed),
                cells = cells(stats.spotifyTracksFailed, stats.soundcloudTracksFailed, stats.tracksFailed),
                accent = if (stats.tracksFailed > 0) colors.error else colors.outlineVariant,
                note = failureBreakdown(job),
            )
            if (stats.tracksUnavailable > 0) {
                // Only SoundCloud reports unavailable (DRM, preview) tracks.
                StatRow(
                    label = stringResource(R.string.stat_unavailable),
                    cells = cells(null, stats.soundcloudTracksUnavailable, stats.tracksUnavailable),
                    accent = colors.tertiary,
                )
            }
            val failedLinks = stats.spotifyFail + stats.soundcloudFail
            if (failedLinks > 0) {
                StatRow(
                    label = stringResource(R.string.job_failed_links),
                    cells = cells(stats.spotifyFail, stats.soundcloudFail, failedLinks),
                    accent = colors.error,
                )
            }
            val lyricsSeen = stats.lyricsOk + stats.lyricsFail + stats.lyricsSkipped > 0
            if (job.options.lyricsMode != LyricsMode.Off || lyricsSeen) {
                HorizontalDivider(color = colors.outlineVariant)
                StatRow(label = stringResource(R.string.stat_lyrics_found), cells = listOf(stats.lyricsOk.toString()))
                StatRow(label = stringResource(R.string.stat_lyrics_missing), cells = listOf(stats.lyricsFail.toString()))
                StatRow(label = stringResource(R.string.stat_lyrics_skipped), cells = listOf(stats.lyricsSkipped.toString()))
            }
        }
    }
}

/** "Rate limited (3) · Network problem (1)", or null when every failure is a plain one. */
@Composable
private fun failureBreakdown(job: Job): String? {
    val counts = job.failedCounts
        .filter { (category, count) -> category != "unavailable" && count > 0 }
        .toList()
        .sortedBy { failureCategoryOrder(it.first) }
    if (counts.all { it.first == "failed" }) return null
    return counts.map { (category, count) ->
        stringResource(R.string.failure_group_title, failureCategoryName(category), count)
    }.joinToString(" · ")
}

/** A label (wrapped, never cut) and right-aligned values; the last value is the total. */
@Composable
private fun StatRow(
    label: String,
    cells: List<String>,
    accent: Color? = null,
    note: String? = null,
    header: Boolean = false,
) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Box(modifier = Modifier.padding(top = 6.dp).size(8.dp).clip(CircleShape).background(accent ?: Color.Transparent))
        Column(modifier = Modifier.weight(1f)) {
            Text(text = label, style = MaterialTheme.typography.bodyMedium)
            if (note != null) {
                Text(text = note, style = MaterialTheme.typography.bodySmall, color = muted)
            }
        }
        cells.forEachIndexed { index, cell ->
            val total = index == cells.lastIndex
            Text(
                text = cell,
                style = when {
                    header -> MaterialTheme.typography.labelMedium
                    total -> MaterialTheme.typography.titleMedium
                    else -> MaterialTheme.typography.bodyMedium
                },
                color = if (header || !total) muted else Color.Unspecified,
                textAlign = TextAlign.End,
                maxLines = 1,
                modifier = Modifier.width(if (total) 56.dp else 76.dp),
            )
        }
    }
}
