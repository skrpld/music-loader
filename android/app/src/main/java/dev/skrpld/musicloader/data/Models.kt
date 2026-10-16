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

/** What a job does with tracks already in the library (the `existing` job option). */
enum class ExistingTracks(val wire: String) {
    Keep("keep"),
    Outdated("outdated"),
    All("all");

    companion object {
        fun fromWire(value: String?): ExistingTracks? = entries.firstOrNull { it.wire == value }
    }
}

@Serializable
data class JobOptions(
    val lyrics: String = LyricsMode.Strict.wire,
    /** The same as [existing] "all"; what servers before [Features.ExistingTracks] understand. */
    val recheck: Boolean = false,
    /** [ExistingTracks] (needs [Features.ExistingTracks]); missing from an older server. */
    val existing: String? = null,
    @SerialName("soundcloud_reposts") val soundcloudReposts: Boolean = false,
    @SerialName("soundcloud_likes") val soundcloudLikes: Boolean = false,
    @SerialName("soundcloud_fallback") val soundcloudFallback: Boolean = false,
    /** The server queues failed tracks again after a cooldown (needs [Features.AutoRetry]). */
    @SerialName("auto_retry") val autoRetry: Boolean = false,
    /** The job writes a detailed debug log (needs [Features.DebugLog]). */
    @SerialName("debug_log") val debugLog: Boolean = false,
    /** SoundCloud formatting (needs [Features.SoundCloudFormat]; an older server ignores them). */
    @SerialName("soundcloud_feat") val soundcloudFeat: String = FeatPlacement.Both.wire,
    @SerialName("soundcloud_keep_producers") val soundcloudKeepProducers: Boolean = false,
    @SerialName("soundcloud_keep_versions") val soundcloudKeepVersions: Boolean = true,
    @SerialName("soundcloud_strip_promo") val soundcloudStripPromo: Boolean = true,
    @SerialName("soundcloud_filename") val soundcloudFilename: String = FilenameTemplate.DEFAULT,
    /** Lyrics settings (needs [Features.LyricsSettings]; an older server ignores them). */
    @SerialName("lyrics_providers") val lyricsProviders: List<String> = LyricsProvider.defaultWire,
    @SerialName("lyrics_plain") val lyricsPlain: Boolean = true,
    @SerialName("lyrics_length_tolerance") val lyricsLengthTolerance: Double = LyricsTolerance.DEFAULT,
    @SerialName("lyrics_original_for_versions") val lyricsOriginalForVersions: Boolean = false,
    @SerialName("lyrics_retry_days") val lyricsRetryDays: Int = LyricsRetry.DEFAULT,
    /** Library check only: fix what can be fixed (needs [Features.LibraryCheck]). */
    val fix: Boolean? = null,
    /** Library check only: [CheckRule] names; null means every rule. */
    val rules: List<String>? = null,
) {
    val lyricsMode: LyricsMode get() = LyricsMode.fromWire(lyrics)
    val featPlacement: FeatPlacement get() = FeatPlacement.fromWire(soundcloudFeat)
    val providers: List<LyricsProvider> get() = LyricsProvider.fromWire(lyricsProviders)

    /** An older server sends only [recheck]. */
    val existingTracks: ExistingTracks
        get() = ExistingTracks.fromWire(existing) ?: if (recheck) ExistingTracks.All else ExistingTracks.Keep

    /** Both fields, so an older server still understands "all". */
    fun withExisting(mode: ExistingTracks): JobOptions =
        copy(existing = mode.wire, recheck = mode == ExistingTracks.All)
}

/** Lyrics providers, asked in the order of [JobOptions.lyricsProviders] (see LYRICS_PROVIDERS in config.py). */
enum class LyricsProvider(val wire: String, val label: String) {
    Lrclib("lrclib", "LRCLIB"),
    Musixmatch("musixmatch", "Musixmatch"),
    Genius("genius", "Genius");

    companion object {
        val defaultWire: List<String> = entries.map { it.wire }

        /** Known names, each once, in order; nothing usable means every provider. */
        fun fromWire(values: List<String>?): List<LyricsProvider> =
            values.orEmpty().mapNotNull { value -> entries.firstOrNull { it.wire == value } }.distinct()
                .ifEmpty { entries.toList() }
    }
}

/** Loose mode: the largest length difference for plain lyrics, in seconds (2-60 on the server). */
object LyricsTolerance {
    const val DEFAULT = 10.0
    val choices = listOf(5.0, 10.0, 20.0, 30.0)

    fun sanitize(value: Double?): Double = value?.takeIf { it in 2.0..60.0 } ?: DEFAULT
}

/** Days before a track whose lyrics were not found is searched again; 0 = on every run. */
object LyricsRetry {
    const val DEFAULT = 7
    val choices = listOf(0, 1, 7, 30)

    fun sanitize(value: Int?): Int = value?.takeIf { it in 0..365 } ?: DEFAULT
}

/** Where featured artists of a SoundCloud track go: title and artist tag, artist tag only, title only. */
enum class FeatPlacement(val wire: String) {
    Both("both"),
    Artist("artist"),
    Title("title");

    companion object {
        fun fromWire(value: String?): FeatPlacement = entries.firstOrNull { it.wire == value } ?: Both
    }
}

/** File name template for SoundCloud tracks; the same rules as validate_filename_template in config.py. */
object FilenameTemplate {
    const val DEFAULT = "{track} - {title}"
    private const val MAX_LENGTH = 200
    private val FIELDS = setOf("track", "title", "artist", "album", "album_artist")
    private val FIELD = Regex("""\{([^{}:!]*)(?::([^{}]*))?\}""")
    private val TRACK_FORMAT = Regex("0?[1-9]")

    fun isValid(template: String): Boolean {
        if (template.isBlank() || template.length > MAX_LENGTH) return false
        if (template.any { it == '/' || it == '\\' || it.code < 32 }) return false
        var titles = 0
        for (match in FIELD.findAll(template)) {
            val name = match.groupValues[1]
            val format = match.groupValues[2]
            if (name !in FIELDS) return false
            if (format.isNotEmpty() && (name != "track" || !TRACK_FORMAT.matches(format))) return false
            if (name == "title") titles++
        }
        // Braces outside a field ("{title", "a}b") are not allowed either.
        val rest = FIELD.replace(template, "")
        return titles == 1 && '{' !in rest && '}' !in rest
    }
}

@Serializable
data class ServerInfo(
    val name: String = "",
    val version: String = "",
    val api: Int = 0,
    @SerialName("music_dir") val musicDir: String = "",
    val busy: Boolean = false,
    val queued: Int = 0,
    /** Optional abilities of the server; an older server sends none. */
    val features: List<String> = emptyList(),
)

/** Names in [ServerInfo.features] and [ServerState.features]. */
object Features {
    /** `POST /jobs/<id>/retry`. */
    const val Retry = "retry"

    /** The `auto_retry` job option. */
    const val AutoRetry = "auto_retry"

    /** The `debug_log` job option and `GET /jobs/<id>/debug-log`. */
    const val DebugLog = "debug_log"

    /** The SoundCloud formatting job options (`soundcloud_feat` ... `soundcloud_filename`). */
    const val SoundCloudFormat = "soundcloud_format"

    /** The lyrics job options (`lyrics_providers` ... `lyrics_retry_days`). */
    const val LyricsSettings = "lyrics_settings"

    /** `GET /artists` and `POST /artists/<id>/<action>`, and the job's `artists` notes. */
    const val Artists = "artists"

    /** The `existing` job option (keep / outdated / all) and the outdated counters. */
    const val ExistingTracks = "existing_tracks"

    /** Jobs of type "check": the library check and its fix (see check.py). */
    const val LibraryCheck = "library_check"
}

/** What a job does: downloads links, or checks the library. */
object JobType {
    const val Download = "download"
    const val Check = "check"
}

/** Rules of the library check, in the server's order (RULES in check.py). */
enum class CheckRule(val wire: String) {
    Id("id"),
    Cover("cover"),
    Lyrics("lyrics"),
    Artist("artist"),
    Format("format"),
    Duplicates("duplicates"),
    /** Asks SoundCloud and Spotify whether the tracks still exist. */
    Available("available");

    companion object {
        fun fromWire(values: List<String>?): Set<CheckRule> =
            values.orEmpty().mapNotNull { value -> entries.firstOrNull { it.wire == value } }.toSet()
    }
}

/** A track with a problem; [link] opens it on its platform. */
@Serializable
data class CheckItem(
    val artist: String = "",
    val title: String = "",
    val path: String = "",
    val link: String = "",
    val service: String = "",
    val fixable: Boolean = false,
    val detail: String = "",
) {
    /** "Artist — Title", or the file path when the tags have neither. */
    val name: String
        get() = listOf(artist, title).filter { it.isNotBlank() }.joinToString(" — ").ifBlank { path }
}

/** One kind of problem; [items] holds at most 500 of the [count] tracks (the log file has all). */
@Serializable
data class CheckGroup(
    val kind: String = "",
    val rule: String = "",
    val label: String = "",
    val count: Int = 0,
    val fixable: Int = 0,
    val items: List<CheckItem> = emptyList(),
)

/**
 * The report of a library check. A job summary carries only the numbers; the job
 * detail adds [rules], [groups] and the [log] path.
 */
@Serializable
data class CheckReport(
    val rules: List<String> = emptyList(),
    val offline: Boolean = false,
    val fix: Boolean = false,
    val tracks: Int = 0,
    val problems: Int = 0,
    val fixable: Int = 0,
    /** Problems the fix removed; null when the job did not fix. */
    val fixed: Int? = null,
    /** Problems before the fix; null when the job did not fix. */
    val before: Int? = null,
    /** Tracks whose availability could not be asked. */
    val unchecked: Int = 0,
    val log: String? = null,
    val groups: List<CheckGroup> = emptyList(),
)

/** What a retry queues: every link of the job again, or only the failures worth another try. */
enum class RetryScope(val wire: String) {
    All("all"),
    Failed("failed"),
}

@Serializable
data class RetryRequest(val scope: String)

/** A track (or link) that failed. [category] is "rate_limited", "network" or "failed". */
@Serializable
data class FailedItem(
    val url: String = "",
    val service: String = "",
    val title: String = "",
    val category: String = "failed",
    val retryable: Boolean = false,
)

@Serializable
data class JobLink(
    val url: String = "",
    val service: String = "",
    val kind: String = "",
)

/** Link progress: resolved links while [resolving], then finished links. */
@Serializable
data class QueueProgress(
    val completed: Int = 0,
    val total: Int = 0,
    val phase: String = "",
) {
    val resolving: Boolean get() = phase == "resolving"
}

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
    /** Tracks SoundCloud does not give out (DRM, preview, blocked): skipped, not failed. */
    @SerialName("soundcloud_tracks_unavailable") val soundcloudTracksUnavailable: Int = 0,
    /** Outdated tracks a job with [ExistingTracks.Keep] left as they were (already in "skipped"). */
    @SerialName("spotify_tracks_outdated") val spotifyTracksOutdated: Int = 0,
    @SerialName("soundcloud_tracks_outdated") val soundcloudTracksOutdated: Int = 0,
) {
    val tracksOutdated: Int get() = spotifyTracksOutdated + soundcloudTracksOutdated
    val tracksDone: Int get() = spotifyTracksDone + soundcloudTracksDone
    val tracksSkipped: Int get() = spotifyTracksSkipped + soundcloudTracksSkipped
    val tracksFailed: Int get() = spotifyTracksFailed + soundcloudTracksFailed
    val tracksUnavailable: Int get() = soundcloudTracksUnavailable
    val tracksProcessed: Int get() = tracksDone + tracksSkipped + tracksFailed + tracksUnavailable

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
    @SerialName("unavailable_log") val unavailableLog: String? = null,
    /** Path of the job's debug log on the server, once the job has started writing it. */
    @SerialName("debug_log") val debugLog: String? = null,
    @SerialName("failed_counts") val failedCounts: Map<String, Int> = emptyMap(),
    @SerialName("retryable_count") val retryableCount: Int = 0,
    @SerialName("failed_items") val failedItems: List<FailedItem> = emptyList(),
    /** 0 for a job somebody queued, n for the nth automatic retry. */
    val attempt: Int = 0,
    @SerialName("max_attempts") val maxAttempts: Int = 0,
    @SerialName("retry_of") val retryOf: String? = null,
    /** When the server queues the failed tracks again (epoch ms), while it waits for that. */
    @SerialName("retry_at") val retryAt: Long? = null,
    /** Artist decisions made during the job (needs [Features.Artists]). */
    val artists: JobArtists = JobArtists(),
    /** [JobType]; an older server sends none, its jobs are downloads. */
    val type: String = JobType.Download,
    /** The report of a check job, once it has one. */
    val check: CheckReport? = null,
) {
    val status: JobStatus get() = JobStatus.fromWire(statusWire)

    val isCheck: Boolean get() = type == JobType.Check

    /** A finished job whose failed tracks the server will queue again by itself. */
    val isWaitingForRetry: Boolean get() = status.isFinished && retryAt != null
}

@Serializable
data class ServerState(
    val revision: Long = 0,
    val busy: Boolean = false,
    val jobs: List<Job> = emptyList(),
    val active: Job? = null,
    val features: List<String> = emptyList(),
)

@Serializable
data class SubmitRequest(
    val links: List<String>,
    val options: JobOptions,
)

@Serializable
data class CheckRequest(
    val options: JobOptions,
    val type: String = JobType.Check,
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

/** What a job decided about SoundCloud accounts, for its summary. */
@Serializable
data class JobArtists(
    /** Accounts that got an artist name. */
    val alias: List<String> = emptyList(),
    /** Accounts with a name waiting for the user (Settings → Artists). */
    val suggestion: List<String> = emptyList(),
    /** Tracks whose artist could not be determined. */
    val undetermined: List<String> = emptyList(),
) {
    val isEmpty: Boolean get() = alias.isEmpty() && suggestion.isEmpty() && undetermined.isEmpty()
}

/** How an account's artist name was decided (see accounts.py). */
enum class AccountStatus(val wire: String) {
    Confirmed("confirmed"),
    Suggestion("suggestion"),
    Reuploader("reuploader"),
    NotChecked("not_checked"),
    Nick("nick");

    companion object {
        fun fromWire(value: String?): AccountStatus = entries.firstOrNull { it.wire == value } ?: Nick
    }
}

/** User actions on an account, the same as `music-loader artists <action>`. */
enum class AccountAction(val wire: String) {
    /** Use the given artist name (needs a name). */
    SetName("set"),
    KeepNick("keep"),
    Reuploader("reuploader"),
    Accept("accept"),
    Reject("reject"),
    Unlock("unlock"),
}

/** A SoundCloud account and the artist name its tracks get. */
@Serializable
data class ArtistAccount(
    val id: String = "",
    val handle: String = "",
    val permalink: String = "",
    val url: String = "",
    @SerialName("status") val statusWire: String = "nick",
    val kind: String = "unknown",
    val alias: String? = null,
    /** library, spotify, deezer, manual or title. */
    val source: String? = null,
    val suggestion: String? = null,
    val locked: Boolean = false,
    @SerialName("checked_at") val checkedAt: String? = null,
    val tracks: Int = 0,
) {
    val status: AccountStatus get() = AccountStatus.fromWire(statusWire)
    val name: String get() = handle.ifBlank { permalink.ifBlank { id } }
}

@Serializable
data class ArtistAccountsEnvelope(val accounts: List<ArtistAccount> = emptyList())

@Serializable
data class ArtistAccountEnvelope(val account: ArtistAccount)

@Serializable
data class ArtistNameRequest(val name: String)
