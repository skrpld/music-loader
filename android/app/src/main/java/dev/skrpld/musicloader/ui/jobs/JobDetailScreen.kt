package dev.skrpld.musicloader.ui.jobs

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.Job
import dev.skrpld.musicloader.data.JobStatus
import dev.skrpld.musicloader.data.RetryScope
import dev.skrpld.musicloader.ui.components.CenteredLoading
import dev.skrpld.musicloader.ui.components.EmptyState
import dev.skrpld.musicloader.ui.components.Pill
import dev.skrpld.musicloader.ui.components.SectionHeader
import dev.skrpld.musicloader.ui.formatDateTime
import dev.skrpld.musicloader.ui.formatDuration
import dev.skrpld.musicloader.ui.formatTime
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
    /** The server can retry jobs; false for an older server, which hides the retry actions. */
    canRetry: Boolean,
    onRetry: (Job, RetryScope) -> Unit,
    onCancelRetryWait: (Job) -> Unit,
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
            else -> JobDetailContent(
                job = job,
                contentPadding = padding,
                canRetry = canRetry,
                onRetry = onRetry,
                onCancelRetryWait = onCancelRetryWait,
            )
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun JobDetailContent(
    job: Job,
    contentPadding: PaddingValues,
    canRetry: Boolean,
    onRetry: (Job, RetryScope) -> Unit,
    onCancelRetryWait: (Job) -> Unit,
) {
    val clipboard = rememberClipboardAccess()
    val scope = rememberCoroutineScope()
    val runlogLabel = stringResource(R.string.job_runlog)
    val unavailableLogLabel = stringResource(R.string.job_unavailable_log)
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
        if (job.status == JobStatus.Running) {
            item(key = "progress") {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    shape = MaterialTheme.shapes.extraLarge,
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh),
                ) {
                    Column(modifier = Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                        JobProgressHeader(job, showElapsed = true)
                        LinkQueueProgress(job)
                        ActiveDownloads(job)
                    }
                }
            }
        } else {
            item(key = "status") { StatusCard(job) }
        }
        if (job.isWaitingForRetry) {
            item(key = "retry-wait") { RetryWaitCard(job, onCancel = { onCancelRetryWait(job) }) }
        }
        if (canRetry && job.status.isFinished) {
            item(key = "retry-actions") { RetryActions(job, onRetry) }
        }
        if (job.status != JobStatus.Queued) {
            item(key = "statistics") { JobStatistics(job, Modifier.padding(top = 8.dp)) }
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
        if (job.log.isNotEmpty() || job.errors.isNotEmpty()) {
            item(key = "log-header") { SectionHeader(stringResource(R.string.job_activity)) }
            item(key = "log") { ActivityLog(job) }
        }
        val unavailableLog = job.unavailableLog
        if (unavailableLog != null && job.stats.tracksUnavailable > 0) {
            item(key = "unavailable-log") {
                SegmentedListItem(
                    onClick = { scope.launch { clipboard.write(unavailableLogLabel, unavailableLog) } },
                    shapes = ListItemDefaults.segmentedShapes(index = 0, count = 1),
                    modifier = Modifier.padding(top = 12.dp),
                    leadingContent = { Icon(painterResource(R.drawable.ic_folder), contentDescription = null) },
                    supportingContent = { Text(unavailableLog) },
                    trailingContent = {
                        Icon(
                            painter = painterResource(R.drawable.ic_content_copy),
                            contentDescription = stringResource(R.string.action_copy),
                        )
                    },
                ) {
                    Text(unavailableLogLabel)
                }
            }
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

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun RetryWaitCard(job: Job, onCancel: () -> Unit) {
    val at = job.retryAt ?: return
    Card(
        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        shape = MaterialTheme.shapes.extraLarge,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
    ) {
        Column(modifier = Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Icon(painter = painterResource(R.drawable.ic_schedule), contentDescription = null)
                Column {
                    Text(
                        text = stringResource(R.string.retry_waiting, formatTime(at)),
                        style = MaterialTheme.typography.titleMedium,
                    )
                    Text(
                        text = stringResource(R.string.retry_waiting_attempt, job.attempt + 1, job.maxAttempts),
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
            Text(text = stringResource(R.string.retry_waiting_body), style = MaterialTheme.typography.bodySmall)
            OutlinedButton(
                onClick = onCancel,
                shapes = ButtonDefaults.shapes(),
                modifier = Modifier.align(Alignment.End),
            ) {
                Text(stringResource(R.string.action_cancel_retry))
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun RetryActions(job: Job, onRetry: (Job, RetryScope) -> Unit) {
    FlowRow(
        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (job.retryableCount > 0) {
            Button(onClick = { onRetry(job, RetryScope.Failed) }, shapes = ButtonDefaults.shapes()) {
                Icon(
                    painter = painterResource(R.drawable.ic_refresh),
                    contentDescription = null,
                    modifier = Modifier.size(ButtonDefaults.IconSize),
                )
                Spacer(Modifier.width(ButtonDefaults.IconSpacing))
                Text(stringResource(R.string.action_retry_failed, job.retryableCount))
            }
        }
        OutlinedButton(onClick = { onRetry(job, RetryScope.All) }, shapes = ButtonDefaults.shapes()) {
            Icon(
                painter = painterResource(R.drawable.ic_sync),
                contentDescription = null,
                modifier = Modifier.size(ButtonDefaults.IconSize),
            )
            Spacer(Modifier.width(ButtonDefaults.IconSpacing))
            Text(stringResource(R.string.action_run_again))
        }
    }
}

/** Status, when it happened and how long it took, in two lines; the progress block replaces it while the job runs. */
@Composable
private fun StatusCard(job: Job) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Icon(
                    painter = painterResource(statusIcon(job)),
                    contentDescription = null,
                    tint = statusColor(job),
                    modifier = Modifier.size(24.dp),
                )
                Column {
                    Text(text = statusLabel(job), style = MaterialTheme.typography.titleMediumEmphasized)
                    Text(
                        text = timingLine(job),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            job.message?.let {
                Text(text = it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
            }
        }
    }
}

/** "Created 09.10.26, 14:05", or "Finished 09.10.26, 14:09 · took 4:02". */
@Composable
private fun timingLine(job: Job): String {
    val finished = job.finishedAt ?: return stringResource(R.string.job_created_at, formatDateTime(job.createdAt))
    val started = job.startedAt
        ?: return stringResource(R.string.job_finished_at, formatDateTime(finished))
    return stringResource(R.string.job_finished_took, formatDateTime(finished), formatDuration(finished - started))
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
        if (options.soundcloudFallback) Pill(text = stringResource(R.string.option_fallback), icon = R.drawable.ic_library_music)
        if (options.autoRetry) Pill(text = stringResource(R.string.option_auto_retry), icon = R.drawable.ic_schedule)
    }
}
