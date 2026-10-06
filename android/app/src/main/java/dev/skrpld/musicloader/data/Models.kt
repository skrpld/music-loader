package dev.skrpld.musicloader.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/** JSON settings shared by every call to the music-loader server (see music_loader/server.py). */
val ApiJson = Json {
    ignoreUnknownKeys = true
    coerceInputValues = true
    explicitNulls = false
    encodeDefaults = true
}

data class ServerConfig(val baseUrl: String, val token: String)

enum class LyricsMode(val wire: String) {
    Strict("strict"),
    Loose("loose"),
    Off("off");

    companion object {
        fun fromWire(value: String?): LyricsMode = entries.firstOrNull { it.wire == value } ?: Strict
    }
}

@Serializable
data class JobOptions(
    val lyrics: String = LyricsMode.Strict.wire,
    val recheck: Boolean = false,
    @SerialName("soundcloud_reposts") val soundcloudReposts: Boolean = false,
    @SerialName("soundcloud_likes") val soundcloudLikes: Boolean = false,
) {
    val lyricsMode: LyricsMode get() = LyricsMode.fromWire(lyrics)
}

@Serializable
data class ServerInfo(
    val name: String = "",
    val version: String = "",
    val api: Int = 0,
    @SerialName("music_dir") val musicDir: String = "",
    val busy: Boolean = false,
    val queued: Int = 0,
)

@Serializable
data class JobLink(
    val url: String = "",
    val service: String = "",
    val kind: String = "",
)

@Serializable
data class QueueProgress(
    val completed: Int = 0,
    val total: Int = 0,
)

@Serializable
data class JobStats(
    @SerialName("spotify_ok") val spotifyOk: Int = 0,
    @SerialName("spotify_fail") val spotifyFail: Int = 0,
    @SerialName("soundcloud_ok") val soundcloudOk: Int = 0,
    @SerialName("soundcloud_fail") val soundcloudFail: Int = 0,
    @SerialName("lyrics_ok") val lyricsOk: Int = 0,
    @SerialName("lyrics_fail") val lyricsFail: Int = 0,
    @SerialName("lyrics_skipped") val lyricsSkipped: Int = 0,
    @SerialName("spotify_tracks_total") val spotifyTracksTotal: Int = 0,
    @SerialName("spotify_tracks_done") val spotifyTracksDone: Int = 0,
    @SerialName("spotify_tracks_skipped") val spotifyTracksSkipped: Int = 0,
    @SerialName("spotify_tracks_failed") val spotifyTracksFailed: Int = 0,
    @SerialName("soundcloud_tracks_total") val soundcloudTracksTotal: Int = 0,
    @SerialName("soundcloud_tracks_done") val soundcloudTracksDone: Int = 0,
    @SerialName("soundcloud_tracks_skipped") val soundcloudTracksSkipped: Int = 0,
    @SerialName("soundcloud_tracks_failed") val soundcloudTracksFailed: Int = 0,
) {
    val tracksDone: Int get() = spotifyTracksDone + soundcloudTracksDone
    val tracksSkipped: Int get() = spotifyTracksSkipped + soundcloudTracksSkipped
    val tracksFailed: Int get() = spotifyTracksFailed + soundcloudTracksFailed
    val tracksProcessed: Int get() = tracksDone + tracksSkipped + tracksFailed

    /** The total grows while links are being resolved; it never drops below what was processed. */
    val tracksTotal: Int get() = maxOf(spotifyTracksTotal + soundcloudTracksTotal, tracksProcessed)
}

@Serializable
data class FileProgress(
    val slot: Int = 0,
    val label: String = "",
    val percent: Float = 0f,
    val speed: String = "",
    val eta: String = "",
)

@Serializable
data class LogEntry(
    val seq: Long = 0,
    val time: Long = 0,
    val level: String = "info",
    val source: String? = null,
    val text: String = "",
) {
    val isError: Boolean get() = level == "error"
}

enum class JobStatus {
    Queued,
    Running,
    Completed,
    Cancelled,
    Failed;

    val isFinished: Boolean get() = this == Completed || this == Cancelled || this == Failed

    companion object {
        fun fromWire(value: String): JobStatus = when (value) {
            "queued" -> Queued
            "running" -> Running
            "completed" -> Completed
            "cancelled" -> Cancelled
            else -> Failed
        }
    }
}

/**
 * A job as the server reports it. Lists and the event stream carry summaries; the
 * detail endpoint (and the running job in the event stream) also fill in the log,
 * errors and active downloads.
 */
@Serializable
data class Job(
    val id: String = "",
    @SerialName("status") val statusWire: String = "queued",
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("started_at") val startedAt: Long? = null,
    @SerialName("finished_at") val finishedAt: Long? = null,
    val message: String? = null,
    @SerialName("link_count") val linkCount: Int = 0,
    val services: Map<String, Int> = emptyMap(),
    val links: List<JobLink> = emptyList(),
    val options: JobOptions = JobOptions(),
    val queue: QueueProgress = QueueProgress(),
    val stats: JobStats = JobStats(),
    @SerialName("error_count") val errorCount: Int = 0,
    @SerialName("cancel_requested") val cancelRequested: Boolean = false,
    val rejected: List<String> = emptyList(),
    val files: List<FileProgress> = emptyList(),
    val log: List<LogEntry> = emptyList(),
    val errors: List<LogEntry> = emptyList(),
    val runlog: String? = null,
) {
    val status: JobStatus get() = JobStatus.fromWire(statusWire)
}

@Serializable
data class ServerState(
    val revision: Long = 0,
    val busy: Boolean = false,
    val jobs: List<Job> = emptyList(),
    val active: Job? = null,
)

@Serializable
data class SubmitRequest(
    val links: List<String>,
    val options: JobOptions,
)

@Serializable
data class SubmitResponse(
    val job: Job,
    val rejected: List<String> = emptyList(),
)

@Serializable
data class JobEnvelope(val job: Job)

@Serializable
data class JobsEnvelope(val jobs: List<Job> = emptyList())

@Serializable
data class ErrorBody(
    val error: String = "",
    val rejected: List<String> = emptyList(),
)
