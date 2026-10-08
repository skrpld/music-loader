package dev.skrpld.musicloader.data

import kotlinx.coroutines.flow.Flow

enum class ThemeMode { System, Light, Dark }

/** Where the music is downloaded: by the app itself, or by a `music-loader serve` computer. */
enum class DownloadMode { Phone, Server }

data class AppSettings(
    val mode: DownloadMode = DownloadMode.Phone,
    /** Library folder for [DownloadMode.Phone]; empty means the shared Music folder. */
    val musicDir: String = "",
    val serverUrl: String = "",
    val token: String = "",
    val themeMode: ThemeMode = ThemeMode.System,
    val dynamicColor: Boolean = true,
    /** Options preselected for the next job; "recheck" is never remembered. */
    val jobOptions: JobOptions = JobOptions(),
) {
    val server: ServerConfig?
        get() = if (serverUrl.isNotBlank() && token.isNotBlank()) ServerConfig(serverUrl, token) else null
}

/**
 * Own Spotify application credentials (phone mode): spotDL then uses the official API, which
 * refuses far less often than the web player. Both values or none. The secret never appears in
 * [toString], so it cannot end up in a log by accident.
 */
data class SpotifyCredentials(val clientId: String = "", val clientSecret: String = "") {
    val isSet: Boolean get() = clientId.isNotBlank() && clientSecret.isNotBlank()

    override fun toString(): String = "SpotifyCredentials(set=$isSet)"
}

interface SettingsStore {
    val settings: Flow<AppSettings>

    /** Kept encrypted; read separately from [settings] so the secret is not passed around with it. */
    val spotifyCredentials: Flow<SpotifyCredentials>

    suspend fun setSpotifyCredentials(credentials: SpotifyCredentials)

    suspend fun setMode(mode: DownloadMode)

    suspend fun setMusicDir(path: String)

    suspend fun setServer(url: String, token: String)

    suspend fun setThemeMode(mode: ThemeMode)

    suspend fun setDynamicColor(enabled: Boolean)

    suspend fun setJobOptions(options: JobOptions)
}
