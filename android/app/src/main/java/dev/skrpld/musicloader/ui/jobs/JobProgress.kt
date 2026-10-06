package dev.skrpld.musicloader.ui.jobs

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularWavyProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.LinearWavyProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.WavyProgressIndicatorDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.FileProgress
import dev.skrpld.musicloader.data.Job
import kotlin.math.roundToInt

/** Track progress of a running job, or null while the total is still unknown. */
private fun trackFraction(job: Job): Float? {
    val total = job.stats.tracksTotal
    return if (total > 0) (job.stats.tracksProcessed.toFloat() / total).coerceIn(0f, 1f) else null
}

/** Big wavy circle with the percentage, link and track counters next to it. */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun JobProgressHeader(job: Job, modifier: Modifier = Modifier) {
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
                text = trackSummary(job),
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

/** One row per file that is being downloaded right now. */
@Composable
fun ActiveDownloads(files: List<FileProgress>, modifier: Modifier = Modifier) {
    if (files.isEmpty()) return
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        files.forEach { file ->
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    text = file.label,
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                LinearProgressIndicator(
                    progress = { (file.percent / 100f).coerceIn(0f, 1f) },
                    modifier = Modifier.fillMaxWidth(),
                )
                val details = listOf(file.speed, file.eta).filter { it.isNotBlank() }.joinToString(" · ")
                if (details.isNotEmpty()) {
                    Text(
                        text = details,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

/** Three tiles: downloaded / already had / failed tracks. */
@Composable
fun TrackStatTiles(job: Job, modifier: Modifier = Modifier) {
    val stats = job.stats
    Row(modifier = modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        StatTile(
            value = stats.tracksDone,
            label = stringResource(R.string.stat_downloaded),
            container = MaterialTheme.colorScheme.primaryContainer,
            content = MaterialTheme.colorScheme.onPrimaryContainer,
            modifier = Modifier.weight(1f),
        )
        StatTile(
            value = stats.tracksSkipped,
            label = stringResource(R.string.stat_skipped),
            container = MaterialTheme.colorScheme.secondaryContainer,
            content = MaterialTheme.colorScheme.onSecondaryContainer,
            modifier = Modifier.weight(1f),
        )
        StatTile(
            value = stats.tracksFailed,
            label = stringResource(R.string.stat_failed),
            container = if (stats.tracksFailed > 0) {
                MaterialTheme.colorScheme.errorContainer
            } else {
                MaterialTheme.colorScheme.surfaceContainerHighest
            },
            content = if (stats.tracksFailed > 0) {
                MaterialTheme.colorScheme.onErrorContainer
            } else {
                MaterialTheme.colorScheme.onSurfaceVariant
            },
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun StatTile(value: Int, label: String, container: Color, content: Color, modifier: Modifier = Modifier) {
    Card(
        modifier = modifier,
        shape = MaterialTheme.shapes.large,
        colors = CardDefaults.cardColors(containerColor = container, contentColor = content),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(text = value.toString(), style = MaterialTheme.typography.headlineSmallEmphasized)
            Text(text = label, style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}
