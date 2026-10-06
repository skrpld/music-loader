package dev.skrpld.musicloader.data

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

enum class LinkService { Spotify, SoundCloud }

data class LinkScan(
    val links: List<String> = emptyList(),
    val spotify: Int = 0,
    val soundcloud: Int = 0,
    val unknown: List<String> = emptyList(),
)

/**
 * Client-side preview of what the server will accept. The server validates every
 * link again (music_loader/links.py); this only drives the counters in the form.
 */
object Links {
    private val spotifyUri = Regex("^spotify:(track|album|playlist|artist):[A-Za-z0-9]{22}$")
    private val urlLike = Regex("""(?:https?://|spotify:)\S+""")
    private val soundcloudHosts = setOf(
        "soundcloud.com", "www.soundcloud.com", "m.soundcloud.com",
        "on.soundcloud.com", "snd.sc", "api.soundcloud.com", "api-v2.soundcloud.com",
    )

    fun service(token: String): LinkService? {
        if (spotifyUri.matches(token)) return LinkService.Spotify
        val url = token.toHttpUrlOrNull() ?: return null
        val host = url.host.lowercase()
        return when {
            host == "open.spotify.com" -> LinkService.Spotify
            host in soundcloudHosts -> LinkService.SoundCloud
            else -> null
        }
    }

    fun scan(text: String): LinkScan {
        val links = LinkedHashSet<String>()
        val unknown = mutableListOf<String>()
        var spotify = 0
        var soundcloud = 0
        for (token in text.split(Regex("\\s+"))) {
            if (token.isBlank()) continue
            when (service(token)) {
                LinkService.Spotify -> if (links.add(token)) spotify++
                LinkService.SoundCloud -> if (links.add(token)) soundcloud++
                null -> unknown += token
            }
        }
        return LinkScan(links.toList(), spotify, soundcloud, unknown)
    }

    /** Pulls links out of shared text such as "Listen to … on #SoundCloud https://on.soundcloud.com/…". */
    fun extract(text: String): List<String> =
        urlLike.findAll(text)
            .map { it.value.trimEnd('.', ',', ')', '!', '?', '"', '\'') }
            .filter { service(it) != null }
            .distinct()
            .toList()
}

object ServerUrls {
    const val DEFAULT_PORT = 8765
    private val explicitPort = Regex("""^[a-zA-Z][a-zA-Z0-9+.-]*://(\[[^\]]+]|[^/:?#]+):\d+""")
    private val token = Regex("[A-Za-z0-9._~+/=-]{16,512}")

    /**
     * "192.168.1.10" -> "http://192.168.1.10:8765"; a URL with a scheme or port is kept
     * as entered (a reverse proxy may serve the API under a path). Null when invalid.
     */
    fun normalize(input: String): String? {
        val trimmed = input.trim().trimEnd('/')
        if (trimmed.isEmpty()) return null
        val hasScheme = "://" in trimmed
        val candidate = if (hasScheme) trimmed else "http://$trimmed"
        val url = candidate.toHttpUrlOrNull() ?: return null
        val result = if (!hasScheme && !explicitPort.containsMatchIn(candidate)) {
            url.newBuilder().port(DEFAULT_PORT).build()
        } else {
            url
        }
        return result.toString().trimEnd('/')
    }

    fun isCleartext(url: String): Boolean = url.trim().startsWith("http://", ignoreCase = true)

    fun isValidToken(value: String): Boolean = token.matches(value.trim())
}
