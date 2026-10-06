package dev.skrpld.musicloader.data

/** The downloader inside the app ([DownloadMode.Phone]); see engine/LocalEngine.kt. */
interface LocalBackend {
    /** The shared Music folder, used when no folder is chosen. */
    val defaultMusicDir: String

    /** Whether the app may write to the music folder. */
    fun hasStorageAccess(): Boolean

    /** Starts the downloader once (later calls only switch the folder) and returns its address. */
    suspend fun start(musicDir: String): ServerConfig

    /** Keeps the app running while the queue is worked off. */
    fun onJobSubmitted()
}

class StorageAccessException : IllegalStateException("The app has no access to the music folder")
