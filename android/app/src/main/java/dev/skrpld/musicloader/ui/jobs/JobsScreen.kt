package dev.skrpld.musicloader.ui.jobs

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
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
import androidx.compose.material3.MediumFlexibleTopAppBar
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedListItem
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.Connection
import dev.skrpld.musicloader.data.Job
import dev.skrpld.musicloader.data.JobStatus
import dev.skrpld.musicloader.data.ServerState
import dev.skrpld.musicloader.data.serverState
import dev.skrpld.musicloader.ui.components.CenteredLoading
import dev.skrpld.musicloader.ui.components.ConnectionBanner
import dev.skrpld.musicloader.ui.components.EmptyState
import dev.skrpld.musicloader.ui.components.SectionHeader
import dev.skrpld.musicloader.ui.formatDateTime

@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun JobsScreen(
    connection: Connection,
    listState: LazyListState,
    selectedId: String?,
    onSelect: (String) -> Unit,
    onCancel: (Job) -> Unit,
    onAddLinks: () -> Unit,
    onOpenSettings: () -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val scrollBehavior = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
    val state = connection.serverState
    Scaffold(
        modifier = modifier.nestedScroll(scrollBehavior.nestedScrollConnection),
        topBar = {
            MediumFlexibleTopAppBar(
                title = { Text(stringResource(R.string.nav_jobs)) },
                subtitle = state?.let { { Text(queueSubtitle(it)) } },
                scrollBehavior = scrollBehavior,
            )
        },
    ) { padding ->
        when {
            state == null && (connection == Connection.Connecting || connection == Connection.Starting) ->
                CenteredLoading(Modifier.padding(padding))
            state == null -> Column(Modifier.padding(padding).padding(16.dp)) {
                ConnectionBanner(connection = connection, onRetry = onRetry, onOpenSettings = onOpenSettings)
            }
            else -> JobList(
                state = state,
                connection = connection,
                listState = listState,
                selectedId = selectedId,
                contentPadding = padding,
                onSelect = onSelect,
                onCancel = onCancel,
                onAddLinks = onAddLinks,
                onOpenSettings = onOpenSettings,
                onRetry = onRetry,
            )
        }
    }
}

@Composable
private fun queueSubtitle(state: ServerState): String {
    val running = state.jobs.count { it.status == JobStatus.Running }
    val queued = state.jobs.count { it.status == JobStatus.Queued }
    return when {
        running == 0 && queued == 0 -> stringResource(R.string.jobs_idle)
        else -> stringResource(R.string.jobs_counts, running, queued)
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun JobList(
    state: ServerState,
    connection: Connection,
    listState: LazyListState,
    selectedId: String?,
    contentPadding: PaddingValues,
    onSelect: (String) -> Unit,
    onCancel: (Job) -> Unit,
    onAddLinks: () -> Unit,
    onOpenSettings: () -> Unit,
    onRetry: () -> Unit,
) {
    val active = state.active ?: state.jobs.firstOrNull { it.status == JobStatus.Running }
    val queued = state.jobs.filter { it.status == JobStatus.Queued }.reversed()
    val finished = state.jobs.filter { it.status.isFinished }
    LazyColumn(
        state = listState,
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(
            start = 16.dp,
            end = 16.dp,
            top = contentPadding.calculateTopPadding() + 8.dp,
            bottom = contentPadding.calculateBottomPadding() + 24.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(ListItemDefaults.SegmentedGap),
    ) {
        if (connection !is Connection.Online) {
            item(key = "banner") {
                ConnectionBanner(
                    connection = connection,
                    onRetry = onRetry,
                    onOpenSettings = onOpenSettings,
                    modifier = Modifier.padding(bottom = 8.dp),
                )
            }
        }
        if (active != null) {
            item(key = "active") {
                ActiveJobCard(
                    job = active,
                    onClick = { onSelect(active.id) },
                    onCancel = { onCancel(active) },
                    modifier = Modifier.padding(bottom = 8.dp),
                )
            }
        }
        if (queued.isNotEmpty()) {
            item(key = "queued-header") { SectionHeader(stringResource(R.string.jobs_queued)) }
            itemsIndexed(queued, key = { _, job -> job.id }) { index, job ->
                JobRow(
                    job = job,
                    index = index,
                    count = queued.size,
                    selected = job.id == selectedId,
                    onClick = { onSelect(job.id) },
                    trailing = {
                        IconButton(onClick = { onCancel(job) }) {
                            Icon(
                                painter = painterResource(R.drawable.ic_close),
                                contentDescription = stringResource(R.string.action_cancel_job),
                            )
                        }
                    },
                )
            }
        }
        if (finished.isNotEmpty()) {
            item(key = "finished-header") { SectionHeader(stringResource(R.string.jobs_finished)) }
            itemsIndexed(finished, key = { _, job -> job.id }) { index, job ->
                JobRow(
                    job = job,
                    index = index,
                    count = finished.size,
                    selected = job.id == selectedId,
                    onClick = { onSelect(job.id) },
                )
            }
        }
        if (state.jobs.isEmpty()) {
            item(key = "empty") {
                EmptyState(
                    icon = R.drawable.ic_library_music,
                    title = stringResource(R.string.jobs_empty_title),
                    body = stringResource(R.string.jobs_empty_body),
                    action = {
                        Button(onClick = onAddLinks, shapes = ButtonDefaults.shapes()) {
                            Text(stringResource(R.string.action_add_links))
                        }
                    },
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun ActiveJobCard(job: Job, onClick: () -> Unit, onCancel: () -> Unit, modifier: Modifier = Modifier) {
    Card(
        onClick = onClick,
        modifier = modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.extraLarge,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHigh),
    ) {
        Column(modifier = Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = statusLabel(job),
                        style = MaterialTheme.typography.labelLarge,
                        color = statusColor(job),
                    )
                    Text(
                        text = jobTitle(job),
                        style = MaterialTheme.typography.titleLargeEmphasized,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            JobProgressHeader(job)
            LinkQueueProgress(job)
            ActiveDownloads(job)
            if (!job.cancelRequested) {
                OutlinedButton(
                    onClick = onCancel,
                    shapes = ButtonDefaults.shapes(),
                    modifier = Modifier.align(Alignment.End),
                ) {
                    Icon(
                        painter = painterResource(R.drawable.ic_cancel),
                        contentDescription = null,
                        modifier = Modifier.padding(end = 8.dp),
                    )
                    Text(stringResource(R.string.action_cancel_job))
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun JobRow(
    job: Job,
    index: Int,
    count: Int,
    selected: Boolean,
    onClick: () -> Unit,
    trailing: (@Composable () -> Unit)? = null,
) {
    SegmentedListItem(
        selected = selected,
        onClick = onClick,
        shapes = ListItemDefaults.segmentedShapes(index = index, count = count),
        leadingContent = {
            Icon(
                painter = painterResource(statusIcon(job)),
                contentDescription = statusLabel(job),
                tint = statusColor(job),
            )
        },
        overlineContent = { Text(formatDateTime(job.finishedAt ?: job.createdAt)) },
        supportingContent = {
            Text(
                text = if (job.status == JobStatus.Queued) statusLabel(job) else trackSummary(job),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        },
        trailingContent = trailing,
    ) {
        Text(text = jobTitle(job), maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}
