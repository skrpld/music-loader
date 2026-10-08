package dev.skrpld.musicloader.data

import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.emitAll
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

sealed interface Connection {
    data object NotConfigured : Connection

    /** Phone mode: the app may not write to the music folder yet. */
    data object NeedsStorageAccess : Connection

    data object Connecting : Connection

    /** Phone mode: the downloader is starting (the first start unpacks its tools). */
    data object Starting : Connection

    /** [local]: the downloader inside the app, not a server. */
    data class Online(val state: ServerState, val local: Boolean = false) : Connection

    /** The stream is down; [lastState] is what the server reported before, if anything. */
    data class Offline(
        val unauthorized: Boolean,
        val message: String?,
        val lastState: ServerState?,
        val local: Boolean = false,
    ) : Connection
}

val Connection.serverState: ServerState?
    get() = when (this) {
        is Connection.Online -> state
        is Connection.Offline -> lastState
        else -> null
    }

/** Whether the server announced [feature] (see [Features]); an older server announces none. */
fun Connection.supports(feature: String): Boolean = serverState?.features?.contains(feature) == true

class NotConfiguredException : IllegalStateException("The server is not configured")

/**
 * Live state over the event stream, reconnecting with backoff, plus the job actions.
 *
 * Both modes speak the same HTTP API: a `music-loader serve` computer, or the
 * downloader inside the app ([LocalBackend]) on 127.0.0.1.
 */
class ServerRepository(
    private val settingsStore: SettingsStore,
    private val api: MusicLoaderApi,
    private val local: LocalBackend,
    scope: CoroutineScope,
) {
    private sealed interface Target {
        data class Local(val musicDir: String) : Target

        data class Remote(val server: ServerConfig?) : Target
    }

    private fun AppSettings.target(): Target = when (mode) {
        DownloadMode.Phone -> Target.Local(musicDir.ifBlank { local.defaultMusicDir })
        DownloadMode.Server -> Target.Remote(server)
    }

    private val retryRequests = MutableSharedFlow<Unit>(extraBufferCapacity = 1)

    init {
        // The phone's downloader uses the Spotify credentials from Settings; a server has its own.
        scope.launch {
            settingsStore.spotifyCredentials.collect { local.setSpotifyCredentials(it.clientId, it.clientSecret) }
        }
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    val connection: StateFlow<Connection> = settingsStore.settings
        .map { it.target() }
        .distinctUntilChanged()
        .flatMapLatest { target ->
            when (target) {
                is Target.Local -> localStream(target.musicDir)
                is Target.Remote ->
                    if (target.server == null) flowOf(Connection.NotConfigured) else stream(target.server, isLocal = false)
            }
        }
        // The stream only runs while something (the UI, the download service) watches it.
        .stateIn(scope, SharingStarted.WhileSubscribed(5_000), Connection.Connecting)

    private fun localStream(musicDir: String): Flow<Connection> = flow {
        while (true) {
            if (!local.hasStorageAccess()) {
                emit(Connection.NeedsStorageAccess)
                // The app checks again when it comes back from the permission screen.
                retryRequests.first()
                continue
            }
            emit(Connection.Starting)
            val server = try {
                local.start(musicDir)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                emit(Connection.Offline(unauthorized = false, message = e.message, lastState = null, local = true))
                retryRequests.first()
                continue
            }
            emitAll(stream(server, isLocal = true))
        }
    }

    private fun stream(server: ServerConfig, isLocal: Boolean): Flow<Connection> = flow {
        emit(Connection.Connecting)
        var lastState: ServerState? = null
        var failures = 0
        while (true) {
            try {
                api.events(server).collect { state ->
                    failures = 0
                    lastState = state
                    emit(Connection.Online(state, isLocal))
                }
                error("The event stream ended")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val unauthorized = e is ApiException && e.isUnauthorized
                emit(Connection.Offline(unauthorized, e.message, lastState, isLocal))
                if (unauthorized) {
                    // A wrong token does not fix itself: wait for new settings or a manual retry.
                    retryRequests.first()
                } else {
                    failures++
                    val delayMillis = minOf(30_000L, 1_000L shl minOf(failures, 5))
                    withTimeoutOrNull(delayMillis) { retryRequests.first() }
                }
            }
        }
    }

    fun retryNow() {
        retryRequests.tryEmit(Unit)
    }

    private suspend fun server(): ServerConfig = when (val target = settingsStore.settings.first().target()) {
        is Target.Local -> {
            if (!local.hasStorageAccess()) throw StorageAccessException()
            local.start(target.musicDir)
        }
        is Target.Remote -> target.server ?: throw NotConfiguredException()
    }

    suspend fun submit(links: List<String>, options: JobOptions): SubmitResponse {
        val server = server()
        val response = api.submit(server, links, options)
        if (settingsStore.settings.first().mode == DownloadMode.Phone) local.onJobSubmitted()
        return response
    }

    suspend fun job(id: String): Job = api.job(server(), id)

    /** Queues a finished job again and returns the new job. */
    suspend fun retry(id: String, scope: RetryScope): Job {
        val job = api.retry(server(), id, scope)
        if (settingsStore.settings.first().mode == DownloadMode.Phone) local.onJobSubmitted()
        return job
    }

    suspend fun cancel(id: String): Job = api.cancel(server(), id)

    suspend fun delete(id: String) = api.delete(server(), id)

    /** Checks a server that is not saved yet. */
    suspend fun test(server: ServerConfig): ServerInfo = api.info(server)
}
