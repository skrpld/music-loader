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
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.map

private val Context.settingsDataStore: DataStore<Preferences> by preferencesDataStore(name = "settings")

/**
 * Settings in the app's private storage; the app opts out of backups, so the token stays on the device.
 * The Spotify credentials are additionally encrypted with a Keystore key ([SecretBox]).
 */
class DataStoreSettings(context: Context, private val secrets: SecretBox = SecretBox()) : SettingsStore {
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
                    soundcloudFallback = prefs[SOUNDCLOUD_FALLBACK] ?: false,
                    autoRetry = prefs[AUTO_RETRY] ?: false,
                ),
            )
        }

    override val spotifyCredentials: Flow<SpotifyCredentials> = store.data
        .catch { error -> if (error is IOException) emit(emptyPreferences()) else throw error }
        .map { prefs ->
            SpotifyCredentials(
                clientId = prefs[SPOTIFY_CLIENT_ID]?.let(secrets::decrypt).orEmpty(),
                clientSecret = prefs[SPOTIFY_CLIENT_SECRET]?.let(secrets::decrypt).orEmpty(),
            )
        }
        .distinctUntilChanged()
        .flowOn(Dispatchers.Default)

    override suspend fun setSpotifyCredentials(credentials: SpotifyCredentials) {
        if (!credentials.isSet) {
            store.edit {
                it.remove(SPOTIFY_CLIENT_ID)
                it.remove(SPOTIFY_CLIENT_SECRET)
            }
            return
        }
        // Encrypted before the edit, so a broken keystore leaves the old values alone.
        val id = secrets.encrypt(credentials.clientId.trim())
        val secret = secrets.encrypt(credentials.clientSecret.trim())
        store.edit {
            it[SPOTIFY_CLIENT_ID] = id
            it[SPOTIFY_CLIENT_SECRET] = secret
        }
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
            it[SOUNDCLOUD_FALLBACK] = options.soundcloudFallback
            it[AUTO_RETRY] = options.autoRetry
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
        val SOUNDCLOUD_FALLBACK = booleanPreferencesKey("soundcloud_fallback")
        val AUTO_RETRY = booleanPreferencesKey("auto_retry")
        val SPOTIFY_CLIENT_ID = stringPreferencesKey("spotify_client_id_enc")
        val SPOTIFY_CLIENT_SECRET = stringPreferencesKey("spotify_client_secret_enc")
    }
}
