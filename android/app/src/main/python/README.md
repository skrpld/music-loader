# Android-only Python modules

Stand-ins for packages that the dependencies import but that have no Android
build. They are part of the app only (Chaquopy adds this folder next to the
`music_loader` package); the desktop tool uses the real packages.

- `curl_cffi` - spotDL's built-in Spotify client (spotapi) and soundcloud-v2
  talk through curl_cffi's browser impersonation. The stand-in offers the same
  session interface on top of `requests`, without the impersonation, and with
  a timeout (10 s to connect, 30 s to read; `requests` has none: a stalled
  connection would hold a job for ever). yt-dlp
  sees its version (0.0.0) as unsupported and does not use it.
- `pymongo` - imported by spotapi for an optional MongoDB cache that spotDL
  never uses.
