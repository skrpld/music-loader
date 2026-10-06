package dev.skrpld.musicloader.data

import kotlinx.coroutines.flow.Flow

enum class ThemeMode { System, Light, Dark }

data class AppSettings(
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

interface SettingsStore {
    val settings: Flow<AppSettings>

    suspend fun setServer(url: String, token: String)

    suspend fun setThemeMode(mode: ThemeMode)

    suspend fun setDynamicColor(enabled: Boolean)

    suspend fun setJobOptions(options: JobOptions)
}
