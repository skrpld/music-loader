package dev.skrpld.musicloader.ui.jobs

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedListItem
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.Job
import dev.skrpld.musicloader.data.JobStatus
import dev.skrpld.musicloader.data.LogEntry
import dev.skrpld.musicloader.ui.components.CenteredLoading
import dev.skrpld.musicloader.ui.components.EmptyState
import dev.skrpld.musicloader.ui.components.Pill
import dev.skrpld.musicloader.ui.components.SectionHeader
import dev.skrpld.musicloader.ui.formatClock
import dev.skrpld.musicloader.ui.formatDateTime
import dev.skrpld.musicloader.ui.formatDuration
import dev.skrpld.musicloader.ui.rememberClipboardAccess
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun JobDetailScreen(
    selected: SelectedJob?,
    showBack: Boolean,
    onBack: () -> Unit,
    onCancel: (Job) -> Unit,
    onDelete: (Job) -> Unit,
    modifier: Modifier = Modifier,
) {
    val job = selected?.job
    val scrollBehavior = TopAppBarDefaults.pinnedScrollBehavior()
    Scaffold(
        modifier = modifier.nestedScroll(scrollBehavior.nestedScrollConnection),
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = if (job != null) jobTitle(job) else stringResource(R.string.job_details),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                subtitle = { if (job != null) Text(statusLabel(job)) },
                navigationIcon = {
                    if (showBack) {
                        IconButton(onClick = onBack) {
                            Icon(
                                painter = painterResource(R.drawable.ic_arrow_back),
                                contentDescription = stringResource(R.string.action_back),
                            )
                        }
                    }
                },
                actions = {
                    if (job != null && !job.status.isFinished && !job.cancelRequested) {
                        IconButton(onClick = { onCancel(job) }) {
                            Icon(
                                painter = painterResource(R.drawable.ic_cancel),
                                contentDescription = stringResource(R.string.action_cancel_job),
                            )
                        }
                    }
                    if (job != null && job.status.isFinished) {
                        IconButton(onClick = { onDelete(job) }) {
                            Icon(
                                painter = painterResource(R.drawable.ic_delete),
                                contentDescription = stringResource(R.string.action_delete_job),
                            )
                        }
                    }
                },
                scrollBehavior = scrollBehavior,
            )
        },
    ) { padding ->
        when {
            job == null && selected?.loading != false -> CenteredLoading(Modifier.padding(padding))
            job == null -> EmptyState(
                icon = R.drawable.ic_error,
                title = stringResource(R.string.job_not_found),
                body = null,
                modifier = Modifier.padding(padding),
            )
            else -> JobDetailContent(job = job, contentPadding = padding)
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun JobDetailContent(job: Job, contentPadding: PaddingValues) {
    val clipboard = rememberClipboardAccess()
    val scope = rememberCoroutineScope()
    val runlogLabel = stringResource(R.string.job_runlog)
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(
            start = 16.dp,
            end = 16.dp,
            top = contentPadding.calculateTopPadding() + 8.dp,
            bottom = contentPadding.calculateBottomPadding() + 24.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(ListItemDefaults.SegmentedGap),
    ) {
        item(key = "status") { StatusCard(job) }
        if (job.status == JobStatus.Running) {
            item(key = "progress") {
                Card(
                    modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                    shape = MaterialTheme.shapes.extraLarge,
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh),
                ) {
                    Column(modifier = Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                        JobProgressHeader(job)
                        LinkQueueProgress(job)
                        ActiveDownloads(job.files)
                    }
                }
            }
        }
        if (job.status != JobStatus.Queued) {
            item(key = "tiles") { TrackStatTiles(job, Modifier.padding(top = 8.dp)) }
            item(key = "services") { ServiceSummary(job) }
        }
        item(key = "options-header") { SectionHeader(stringResource(R.string.job_options)) }
        item(key = "options") { OptionPills(job) }

        item(key = "links-header") {
            SectionHeader(stringResource(R.string.job_links_count, job.links.size))
        }
        itemsIndexed(job.links, key = { index, _ -> "link-$index" }) { index, link ->
            SegmentedListItem(
                shapes = ListItemDefaults.segmentedShapes(index = index, count = job.links.size),
                leadingContent = { Icon(painterResource(R.drawable.ic_link), contentDescription = null) },
                overlineContent = { Text("${serviceName(link.service)} · ${linkKindName(link.kind)}") },
            ) {
                Text(text = link.url, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
        if (job.rejected.isNotEmpty()) {
            item(key = "rejected-header") { SectionHeader(stringResource(R.string.job_rejected)) }
            itemsIndexed(job.rejected, key = { index, _ -> "rejected-$index" }) { index, entry ->
                SegmentedListItem(
                    shapes = ListItemDefaults.segmentedShapes(index = index, count = job.rejected.size),
                    leadingContent = { Icon(painterResource(R.drawable.ic_warning), contentDescription = null) },
                ) {
                    Text(text = entry, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
        }
        if (job.errors.isNotEmpty() || job.errorCount > 0) {
            item(key = "errors-header") {
                SectionHeader(stringResource(R.string.job_errors_count, job.errorCount))
            }
            logEntries("error", job.errors.asReversed())
        }
        if (job.log.isNotEmpty()) {
            item(key = "log-header") { SectionHeader(stringResource(R.string.job_activity)) }
            logEntries("log", job.log.asReversed())
        }
        val runlog = job.runlog
        if (runlog != null && job.errorCount > 0) {
            item(key = "runlog") {
                SegmentedListItem(
                    onClick = { scope.launch { clipboard.write(runlogLabel, runlog) } },
                    shapes = ListItemDefaults.segmentedShapes(index = 0, count = 1),
                    modifier = Modifier.padding(top = 12.dp),
                    leadingContent = { Icon(painterResource(R.drawable.ic_folder), contentDescription = null) },
                    supportingContent = { Text(runlog) },
                    trailingContent = {
                        Icon(
                            painter = painterResource(R.drawable.ic_content_copy),
                            contentDescription = stringResource(R.string.action_copy),
                        )
                    },
                ) {
                    Text(runlogLabel)
                }
            }
        }
    }
}

private fun LazyListScope.logEntries(prefix: String, entries: List<LogEntry>) {
    items(entries, key = { "$prefix-${it.seq}" }) { entry -> LogRow(entry) }
}

@Composable
private fun LogRow(entry: LogEntry) {
    val color = if (entry.isError) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 2.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            text = formatClock(entry.time),
            style = MaterialTheme.typography.labelSmall,
            fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            text = if (entry.source != null) "[${entry.source}] ${entry.text}" else entry.text,
            style = MaterialTheme.typography.bodySmall,
            fontFamily = FontFamily.Monospace,
            color = color,
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun StatusCard(job: Job) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.extraLarge,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(modifier = Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Icon(
                    painter = painterResource(statusIcon(job)),
                    contentDescription = null,
                    tint = statusColor(job),
                    modifier = Modifier.size(32.dp),
                )
                Text(text = statusLabel(job), style = MaterialTheme.typography.titleLargeEmphasized)
            }
            DetailLine(stringResource(R.string.job_created), formatDateTime(job.createdAt))
            job.startedAt?.let { DetailLine(stringResource(R.string.job_started), formatDateTime(it)) }
            job.finishedAt?.let { DetailLine(stringResource(R.string.job_finished), formatDateTime(it)) }
            val started = job.startedAt
            if (started != null) {
                val end = job.finishedAt ?: System.currentTimeMillis()
                DetailLine(stringResource(R.string.job_duration), formatDuration(end - started))
            }
            job.message?.let {
                Text(text = it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
            }
        }
    }
}

@Composable
private fun DetailLine(label: String, value: String) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(text = label, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(text = value, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun ServiceSummary(job: Job) {
    val stats = job.stats
    Card(
        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        shape = MaterialTheme.shapes.large,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (stats.spotifyTracksTotal > 0 || stats.spotifyOk + stats.spotifyFail > 0) {
                DetailLine(
                    "Spotify",
                    stringResource(
                        R.string.service_summary,
                        stats.spotifyTracksDone,
                        stats.spotifyTracksSkipped,
                        stats.spotifyTracksFailed,
                    ),
                )
            }
            if (stats.soundcloudTracksTotal > 0 || stats.soundcloudOk + stats.soundcloudFail > 0) {
                DetailLine(
                    "SoundCloud",
                    stringResource(
                        R.string.service_summary,
                        stats.soundcloudTracksDone,
                        stats.soundcloudTracksSkipped,
                        stats.soundcloudTracksFailed,
                    ),
                )
            }
            DetailLine(
                stringResource(R.string.lyrics_title),
                stringResource(R.string.lyrics_summary, stats.lyricsOk, stats.lyricsFail, stats.lyricsSkipped),
            )
            val failedLinks = stats.spotifyFail + stats.soundcloudFail
            if (failedLinks > 0) {
                DetailLine(stringResource(R.string.job_failed_links), failedLinks.toString())
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun OptionPills(job: Job) {
    val options = job.options
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Pill(
            text = stringResource(R.string.job_option_lyrics, lyricsModeName(options.lyricsMode)),
            icon = R.drawable.ic_lyrics,
        )
        if (options.recheck) Pill(text = stringResource(R.string.option_recheck), icon = R.drawable.ic_sync)
        if (options.soundcloudReposts) Pill(text = stringResource(R.string.option_reposts), icon = R.drawable.ic_repeat)
        if (options.soundcloudLikes) Pill(text = stringResource(R.string.option_likes), icon = R.drawable.ic_favorite)
    }
}
