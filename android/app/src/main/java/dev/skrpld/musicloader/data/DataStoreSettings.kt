package dev.skrpld.musicloader.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import java.io.IOException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.map

private val Context.settingsDataStore: DataStore<Preferences> by preferencesDataStore(name = "settings")

/** Settings in the app's private storage; the app opts out of backups, so the token stays on the device. */
class DataStoreSettings(context: Context) : SettingsStore {
    private val store = context.applicationContext.settingsDataStore

    override val settings: Flow<AppSettings> = store.data
        .catch { error -> if (error is IOException) emit(emptyPreferences()) else throw error }
        .map { prefs ->
            AppSettings(
                // Installs from before the phone mode keep using their server.
                mode = DownloadMode.entries.firstOrNull { it.name == prefs[MODE] }
                    ?: if (prefs[SERVER_URL].isNullOrEmpty()) DownloadMode.Phone else DownloadMode.Server,
                musicDir = prefs[MUSIC_DIR].orEmpty(),
                serverUrl = prefs[SERVER_URL].orEmpty(),
                token = prefs[TOKEN].orEmpty(),
                themeMode = ThemeMode.entries.firstOrNull { it.name == prefs[THEME_MODE] } ?: ThemeMode.System,
                dynamicColor = prefs[DYNAMIC_COLOR] ?: true,
                jobOptions = JobOptions(
                    lyrics = LyricsMode.fromWire(prefs[LYRICS]).wire,
                    soundcloudReposts = prefs[SOUNDCLOUD_REPOSTS] ?: false,
                    soundcloudLikes = prefs[SOUNDCLOUD_LIKES] ?: false,
                ),
            )
        }

    override suspend fun setMode(mode: DownloadMode) {
        store.edit { it[MODE] = mode.name }
    }

    override suspend fun setMusicDir(path: String) {
        store.edit { it[MUSIC_DIR] = path }
    }

    override suspend fun setServer(url: String, token: String) {
        store.edit {
            it[SERVER_URL] = url
            it[TOKEN] = token
        }
    }

    override suspend fun setThemeMode(mode: ThemeMode) {
        store.edit { it[THEME_MODE] = mode.name }
    }

    override suspend fun setDynamicColor(enabled: Boolean) {
        store.edit { it[DYNAMIC_COLOR] = enabled }
    }

    override suspend fun setJobOptions(options: JobOptions) {
        store.edit {
            it[LYRICS] = options.lyrics
            it[SOUNDCLOUD_REPOSTS] = options.soundcloudReposts
            it[SOUNDCLOUD_LIKES] = options.soundcloudLikes
        }
    }

    private companion object {
        val MODE = stringPreferencesKey("mode")
        val MUSIC_DIR = stringPreferencesKey("music_dir")
        val SERVER_URL = stringPreferencesKey("server_url")
        val TOKEN = stringPreferencesKey("token")
        val THEME_MODE = stringPreferencesKey("theme_mode")
        val DYNAMIC_COLOR = booleanPreferencesKey("dynamic_color")
        val LYRICS = stringPreferencesKey("lyrics")
        val SOUNDCLOUD_REPOSTS = booleanPreferencesKey("soundcloud_reposts")
        val SOUNDCLOUD_LIKES = booleanPreferencesKey("soundcloud_likes")
    }
}
