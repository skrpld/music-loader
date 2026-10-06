package dev.skrpld.musicloader.data

import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.json.Json
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources

/** An HTTP error answered by the server; 401 means a missing or wrong token. */
class ApiException(val code: Int, message: String) : IOException(message) {
    val isUnauthorized: Boolean get() = code == 401
    val isNotFound: Boolean get() = code == 404
}

/** Client for the `music-loader serve` HTTP API (see music_loader/server.py). */
class MusicLoaderApi(
    private val client: OkHttpClient,
    private val json: Json = ApiJson,
) {
    // The event stream sends a keep-alive comment every 15 s.
    private val streamClient = client.newBuilder()
        .readTimeout(45, TimeUnit.SECONDS)
        .build()

    suspend fun info(server: ServerConfig): ServerInfo =
        json.decodeFromString(execute(request(server, "info").build()))

    suspend fun jobs(server: ServerConfig): List<Job> =
        json.decodeFromString<JobsEnvelope>(execute(request(server, "jobs").build())).jobs

    suspend fun job(server: ServerConfig, id: String): Job =
        json.decodeFromString<JobEnvelope>(execute(request(server, "jobs", id).build())).job

    suspend fun submit(server: ServerConfig, links: List<String>, options: JobOptions): SubmitResponse {
        val body = json.encodeToString(SubmitRequest.serializer(), SubmitRequest(links, options))
            .toRequestBody(JSON)
        return json.decodeFromString(execute(request(server, "jobs").post(body).build()))
    }

    suspend fun cancel(server: ServerConfig, id: String): Job {
        val body = ByteArray(0).toRequestBody(JSON)
        return json.decodeFromString<JobEnvelope>(
            execute(request(server, "jobs", id, "cancel").post(body).build()),
        ).job
    }

    suspend fun delete(server: ServerConfig, id: String) {
        execute(request(server, "jobs", id).delete().build())
    }

    /** Server-Sent Events: the full server state after every change. */
    fun events(server: ServerConfig): Flow<ServerState> = callbackFlow {
        val request = request(server, "events").header("Accept", "text/event-stream").build()
        val listener = object : EventSourceListener() {
            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                if (type != "state") return
                val state = runCatching { json.decodeFromString<ServerState>(data) }.getOrNull() ?: return
                trySend(state)
            }

            override fun onClosed(eventSource: EventSource) {
                close(IOException("The server closed the event stream"))
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                val error = when {
                    response != null && !response.isSuccessful ->
                        ApiException(response.code, "HTTP ${response.code}")
                    t != null -> t
                    else -> IOException("The event stream failed")
                }
                close(error)
            }
        }
        val source = EventSources.createFactory(streamClient).newEventSource(request, listener)
        awaitClose { source.cancel() }
    }.conflate()

    private fun request(server: ServerConfig, vararg segments: String): Request.Builder =
        Request.Builder()
            .url(endpoint(server, *segments))
            .header("Authorization", "Bearer ${server.token}")

    private suspend fun execute(request: Request): String = suspendCancellableCoroutine { continuation ->
        val call = client.newCall(request)
        continuation.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                continuation.resumeWithException(e)
            }

            override fun onResponse(call: Call, response: Response) {
                val result = runCatching {
                    response.use {
                        val body = it.body.string()
                        if (!it.isSuccessful) {
                            throw ApiException(it.code, errorMessage(body) ?: "HTTP ${it.code}")
                        }
                        body
                    }
                }
                continuation.resumeWith(result)
            }
        })
    }

    private fun errorMessage(body: String): String? =
        runCatching { json.decodeFromString<ErrorBody>(body).error }.getOrNull()?.takeIf { it.isNotBlank() }

    companion object {
        private val JSON = "application/json; charset=utf-8".toMediaType()

        fun endpoint(server: ServerConfig, vararg segments: String): HttpUrl {
            val builder = server.baseUrl.toHttpUrl().newBuilder().addPathSegments("api/v1")
            segments.forEach { builder.addPathSegment(it) }
            return builder.build()
        }
    }
}
