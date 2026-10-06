package dev.skrpld.musicloader.ui

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

private val dateTimeFormat = DateTimeFormatter.ofLocalizedDateTime(FormatStyle.SHORT)
private val clockFormat = DateTimeFormatter.ofPattern("HH:mm:ss")

fun formatDateTime(epochMillis: Long): String =
    dateTimeFormat.format(Instant.ofEpochMilli(epochMillis).atZone(ZoneId.systemDefault()))

fun formatClock(epochMillis: Long): String =
    clockFormat.format(Instant.ofEpochMilli(epochMillis).atZone(ZoneId.systemDefault()))

fun formatDuration(millis: Long): String {
    val seconds = (millis / 1000).coerceAtLeast(0)
    val hours = seconds / 3600
    val minutes = seconds % 3600 / 60
    val rest = seconds % 60
    return if (hours > 0) "%d:%02d:%02d".format(hours, minutes, rest) else "%d:%02d".format(minutes, rest)
}
