package dev.skrpld.musicloader.engine

import android.content.Context
import android.content.Intent
import android.os.Environment
import android.util.Log
import androidx.core.content.ContextCompat
import com.chaquo.python.PyException
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import dev.skrpld.musicloader.data.LocalBackend
import dev.skrpld.musicloader.data.ServerConfig
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

class EngineException(message: String, cause: Throwable? = null) : IOException(message, cause)

/**
 * The downloader inside the app: music_loader (embedded Python, Chaquopy) runs its
 * server on 127.0.0.1, and the app talks to it like to a remote one. Jobs run in the
 * app process with ffmpeg and QuickJS shipped as native libraries; see
 * music_loader/android.py.
 */
class LocalEngine(context: Context) : LocalBackend {
    private val app = context.applicationContext
    private val mutex = Mutex()

    // Held in memory only; handed to the downloader whenever it is (or becomes) available.
    private var spotifyClientId = ""
    private var spotifyClientSecret = ""

    @Suppress("DEPRECATION")
    override val defaultMusicDir: String =
        Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MUSIC).absolutePath

    override fun hasStorageAccess(): Boolean = StorageAccess.granted(app)

    override suspend fun start(musicDir: String): ServerConfig = withContext(Dispatchers.IO) {
        mutex.withLock {
            val result = try {
                if (!Python.isStarted()) Python.start(AndroidPlatform(app))
                Python.getInstance()
                    .getModule("music_loader.android")
                    .callAttr(
                        "start",
                        app.applicationInfo.nativeLibraryDir,
                        app.filesDir.absolutePath,
                        app.cacheDir.absolutePath,
                        musicDir,
                    )
                    .toString()
            } catch (e: PyException) {
                Log.e(TAG, "The downloader did not start", e)
                throw EngineException(e.message ?: "The downloader did not start", e)
            }
            applySpotifyCredentials()
            val endpoint = Json.parseToJsonElement(result).jsonObject
            ServerConfig(
                baseUrl = endpoint.getValue("url").jsonPrimitive.content,
                token = endpoint.getValue("token").jsonPrimitive.content,
            )
        }
    }

    override suspend fun setSpotifyCredentials(clientId: String, clientSecret: String) {
        withContext(Dispatchers.IO) {
            mutex.withLock {
                spotifyClientId = clientId
                spotifyClientSecret = clientSecret
                // Before the first start there is nothing to tell yet: start() does it.
                if (Python.isStarted()) applySpotifyCredentials()
            }
        }
    }

    private fun applySpotifyCredentials() {
        try {
            Python.getInstance()
                .getModule("music_loader.android")
                .callAttr("set_spotify_credentials", spotifyClientId, spotifyClientSecret)
        } catch (e: PyException) {
            // The message is left out on purpose: nothing about the credentials goes to the log.
            Log.w(TAG, "Could not hand the Spotify credentials to the downloader")
        }
    }

    override fun onJobSubmitted() {
        try {
            ContextCompat.startForegroundService(app, Intent(app, DownloadService::class.java))
        } catch (e: IllegalStateException) {
            // Not allowed from the background; the job still runs while the app is open.
            Log.w(TAG, "Could not start the download service", e)
        }
    }

    private companion object {
        const val TAG = "LocalEngine"
    }
}
