package dev.skrpld.musicloader.ui.jobs

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.Job
import dev.skrpld.musicloader.data.JobStatus
import dev.skrpld.musicloader.data.LyricsMode

fun serviceName(service: String): String = when (service) {
    "spotify" -> "Spotify"
    "soundcloud" -> "SoundCloud"
    else -> service
}

@Composable
fun linkKindName(kind: String): String = stringResource(
    when (kind) {
        "track" -> R.string.kind_track
        "album" -> R.string.kind_album
        "playlist" -> R.string.kind_playlist
        "artist" -> R.string.kind_artist
        "set" -> R.string.kind_set
        "profile" -> R.string.kind_profile
        "section" -> R.string.kind_section
        else -> R.string.kind_link
    },
)

/** "Spotify · Album", "SoundCloud · Profile and 2 more links". */
@Composable
fun jobTitle(job: Job): String {
    val first = job.links.firstOrNull() ?: return stringResource(R.string.job_untitled)
    val base = stringResource(R.string.job_link_title, serviceName(first.service), linkKindName(first.kind))
    val more = job.linkCount - 1
    return if (more > 0) pluralStringResource(R.plurals.job_more_links, more, base, more) else base
}

/** "Downloaded: 12 · Already had: 3 · Failed: 1 · Unavailable: 2". */
@Composable
fun trackSummary(job: Job): String {
    val stats = job.stats
    val parts = buildList {
        add(stringResource(R.string.stat_downloaded_count, stats.tracksDone))
        if (stats.tracksSkipped > 0) add(stringResource(R.string.stat_skipped_count, stats.tracksSkipped))
        if (stats.tracksFailed > 0) add(stringResource(R.string.stat_failed_count, stats.tracksFailed))
        if (stats.tracksUnavailable > 0) add(stringResource(R.string.stat_unavailable_count, stats.tracksUnavailable))
    }
    return parts.joinToString(" · ")
}

/** A finished job with failed links or tracks is shown as "done with errors"; unavailable tracks are not failures. */
val Job.hasFailures: Boolean
    get() = stats.tracksFailed > 0 || stats.spotifyFail > 0 || stats.soundcloudFail > 0

@Composable
fun statusLabel(job: Job): String = stringResource(
    when (job.status) {
        JobStatus.Queued -> R.string.status_queued
        JobStatus.Running -> if (job.cancelRequested) R.string.status_cancelling else R.string.status_running
        JobStatus.Completed -> when {
            job.isWaitingForRetry -> R.string.status_waiting_retry
            job.hasFailures -> R.string.status_completed_with_errors
            else -> R.string.status_completed
        }
        JobStatus.Cancelled -> R.string.status_cancelled
        JobStatus.Failed -> R.string.status_failed
    },
)

fun statusIcon(job: Job): Int = when (job.status) {
    JobStatus.Queued -> R.drawable.ic_schedule
    JobStatus.Running -> R.drawable.ic_sync
    JobStatus.Completed -> when {
        job.isWaitingForRetry -> R.drawable.ic_schedule
        job.hasFailures -> R.drawable.ic_warning
        else -> R.drawable.ic_check_circle
    }
    JobStatus.Cancelled -> R.drawable.ic_block
    JobStatus.Failed -> R.drawable.ic_error
}

@Composable
fun statusColor(job: Job): Color {
    val colors = MaterialTheme.colorScheme
    return when (job.status) {
        JobStatus.Queued -> colors.secondary
        JobStatus.Running -> colors.tertiary
        JobStatus.Completed -> when {
            job.isWaitingForRetry -> colors.secondary
            job.hasFailures -> colors.error
            else -> colors.primary
        }
        JobStatus.Cancelled -> colors.onSurfaceVariant
        JobStatus.Failed -> colors.error
    }
}

@Composable
fun lyricsModeName(mode: LyricsMode): String = stringResource(
    when (mode) {
        LyricsMode.Strict -> R.string.lyrics_strict
        LyricsMode.Loose -> R.string.lyrics_loose
        LyricsMode.Off -> R.string.lyrics_off
    },
)

/** Why a track failed, in a word ("Rate limited"); unknown categories read as a plain failure. */
@Composable
fun failureCategoryName(category: String): String = stringResource(
    when (category) {
        "rate_limited" -> R.string.failure_rate_limited
        "network" -> R.string.failure_network
        "unavailable" -> R.string.failure_unavailable
        else -> R.string.failure_failed
    },
)

/** What the category means and whether a retry can help. */
@Composable
fun failureCategoryHint(category: String): String = stringResource(
    when (category) {
        "rate_limited" -> R.string.failure_rate_limited_hint
        "network" -> R.string.failure_network_hint
        "unavailable" -> R.string.failure_unavailable_hint
        else -> R.string.failure_failed_hint
    },
)

/** Retryable categories first, in the order of how likely a retry is to help. */
fun failureCategoryOrder(category: String): Int = when (category) {
    "rate_limited" -> 0
    "network" -> 1
    "unavailable" -> 3
    else -> 2
}
