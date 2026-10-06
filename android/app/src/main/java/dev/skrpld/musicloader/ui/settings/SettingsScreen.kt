package dev.skrpld.musicloader.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ButtonGroupDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeFlexibleTopAppBar
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedListItem
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.ToggleButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.skrpld.musicloader.R
import dev.skrpld.musicloader.data.AppSettings
import dev.skrpld.musicloader.data.ServerUrls
import dev.skrpld.musicloader.data.ThemeMode
import dev.skrpld.musicloader.ui.components.SectionHeader

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    viewModel: SettingsViewModel,
    appVersion: String,
    dynamicColorAvailable: Boolean,
    modifier: Modifier = Modifier,
) {
    val settings by viewModel.settings.collectAsStateWithLifecycle()
    val scrollBehavior = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
    Scaffold(
        modifier = modifier.nestedScroll(scrollBehavior.nestedScrollConnection),
        topBar = {
            LargeFlexibleTopAppBar(
                title = { Text(stringResource(R.string.nav_settings)) },
                scrollBehavior = scrollBehavior,
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .consumeWindowInsets(padding)
                .imePadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            val contentModifier = Modifier.widthIn(max = 640.dp).fillMaxWidth()
            Column(modifier = contentModifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                SectionHeader(stringResource(R.string.settings_server))
                ServerCard(viewModel, settings)
                SectionHeader(stringResource(R.string.settings_appearance))
                Text(
                    text = stringResource(R.string.settings_theme),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 4.dp),
                )
                ThemeSelector(
                    selected = settings?.themeMode ?: ThemeMode.System,
                    onSelect = viewModel::setThemeMode,
                )
                DynamicColorItem(
                    checked = (settings?.dynamicColor ?: true) && dynamicColorAvailable,
                    available = dynamicColorAvailable,
                    onCheckedChange = viewModel::setDynamicColor,
                )
                SectionHeader(stringResource(R.string.settings_about))
                AboutItems(appVersion)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun ServerCard(viewModel: SettingsViewModel, settings: AppSettings?) {
    var tokenVisible by rememberSaveable { mutableStateOf(false) }
    val url = viewModel.normalizedUrl
    Card(
        shape = MaterialTheme.shapes.extraLarge,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            val urlError = viewModel.urlInput.isNotBlank() && url == null
            OutlinedTextField(
                value = viewModel.urlInput,
                onValueChange = viewModel::updateUrl,
                modifier = Modifier.fillMaxWidth(),
                label = { Text(stringResource(R.string.settings_server_url)) },
                placeholder = { Text(stringResource(R.string.settings_server_url_placeholder)) },
                leadingIcon = { Icon(painterResource(R.drawable.ic_dns), contentDescription = null) },
                supportingText = {
                    Text(
                        stringResource(
                            if (urlError) R.string.settings_server_url_invalid else R.string.settings_server_url_hint,
                        ),
                    )
                },
                isError = urlError,
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, autoCorrectEnabled = false),
                shape = MaterialTheme.shapes.large,
            )
            val tokenError = viewModel.tokenInput.isNotBlank() && !viewModel.tokenValid
            OutlinedTextField(
                value = viewModel.tokenInput,
                onValueChange = viewModel::updateToken,
                modifier = Modifier.fillMaxWidth(),
                label = { Text(stringResource(R.string.settings_token)) },
                supportingText = {
                    Text(
                        stringResource(
                            if (tokenError) R.string.settings_token_invalid else R.string.settings_token_hint,
                        ),
                    )
                },
                isError = tokenError,
                singleLine = true,
                visualTransformation = if (tokenVisible) VisualTransformation.None else PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false),
                trailingIcon = {
                    IconButton(onClick = { tokenVisible = !tokenVisible }) {
                        Icon(
                            painter = painterResource(
                                if (tokenVisible) R.drawable.ic_visibility_off else R.drawable.ic_visibility,
                            ),
                            contentDescription = stringResource(
                                if (tokenVisible) R.string.action_hide_token else R.string.action_show_token,
                            ),
                        )
                    }
                },
                shape = MaterialTheme.shapes.large,
            )
            if (url != null && ServerUrls.isCleartext(url)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(
                        painter = painterResource(R.drawable.ic_warning),
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.tertiary,
                        modifier = Modifier.size(20.dp),
                    )
                    Text(
                        text = stringResource(R.string.settings_cleartext_warning),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            TestResult(viewModel.test)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
            ) {
                OutlinedButton(
                    onClick = viewModel::testConnection,
                    shapes = ButtonDefaults.shapes(),
                    enabled = viewModel.canSubmit && viewModel.test != ConnectionTest.Running,
                ) {
                    Text(stringResource(R.string.action_test))
                }
                Button(
                    onClick = viewModel::save,
                    shapes = ButtonDefaults.shapes(),
                    enabled = viewModel.canSubmit && !viewModel.isSaved(settings),
                ) {
                    Text(stringResource(if (viewModel.isSaved(settings)) R.string.action_saved else R.string.action_save))
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun TestResult(test: ConnectionTest) {
    when (test) {
        ConnectionTest.Idle -> Unit
        ConnectionTest.Running -> Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            LoadingIndicator(modifier = Modifier.size(32.dp))
            Text(stringResource(R.string.settings_testing), style = MaterialTheme.typography.bodyMedium)
        }
        is ConnectionTest.Success -> ResultRow(
            icon = R.drawable.ic_check_circle,
            tint = MaterialTheme.colorScheme.primary,
            title = stringResource(R.string.settings_test_ok, test.info.version),
            body = stringResource(R.string.settings_test_library, test.info.musicDir),
        )
        is ConnectionTest.Failure -> ResultRow(
            icon = R.drawable.ic_error,
            tint = MaterialTheme.colorScheme.error,
            title = stringResource(
                if (test.unauthorized) R.string.connection_unauthorized else R.string.settings_test_failed,
            ),
            body = if (test.unauthorized) null else test.message,
        )
    }
}

@Composable
private fun ResultRow(icon: Int, tint: androidx.compose.ui.graphics.Color, title: String, body: String?) {
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Icon(painterResource(icon), contentDescription = null, tint = tint)
        Column {
            Text(title, style = MaterialTheme.typography.bodyMedium)
            if (body != null) {
                Text(body, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun ThemeSelector(selected: ThemeMode, onSelect: (ThemeMode) -> Unit) {
    val modes = ThemeMode.entries
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(ButtonGroupDefaults.ConnectedSpaceBetween),
    ) {
        modes.forEachIndexed { index, mode ->
            val (label, icon) = when (mode) {
                ThemeMode.System -> R.string.theme_system to R.drawable.ic_brightness_auto
                ThemeMode.Light -> R.string.theme_light to R.drawable.ic_light_mode
                ThemeMode.Dark -> R.string.theme_dark to R.drawable.ic_dark_mode
            }
            ToggleButton(
                checked = selected == mode,
                onCheckedChange = { onSelect(mode) },
                modifier = Modifier
                    .weight(1f)
                    .semantics { role = Role.RadioButton },
                icon = { Icon(painterResource(icon), contentDescription = null, modifier = Modifier.size(18.dp)) },
                shapes = when (index) {
                    0 -> ButtonGroupDefaults.connectedLeadingButtonShapes()
                    modes.lastIndex -> ButtonGroupDefaults.connectedTrailingButtonShapes()
                    else -> ButtonGroupDefaults.connectedMiddleButtonShapes()
                },
            ) {
                Text(text = stringResource(label), maxLines = 1)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun DynamicColorItem(checked: Boolean, available: Boolean, onCheckedChange: (Boolean) -> Unit) {
    SegmentedListItem(
        checked = checked,
        onCheckedChange = onCheckedChange,
        shapes = ListItemDefaults.segmentedShapes(index = 0, count = 1),
        enabled = available,
        modifier = Modifier.padding(top = 8.dp),
        leadingContent = { Icon(painterResource(R.drawable.ic_palette), contentDescription = null) },
        supportingContent = {
            Text(
                stringResource(
                    if (available) R.string.settings_dynamic_color_hint else R.string.settings_dynamic_color_unavailable,
                ),
            )
        },
        trailingContent = { Switch(checked = checked, onCheckedChange = null, enabled = available) },
    ) {
        Text(stringResource(R.string.settings_dynamic_color))
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun AboutItems(appVersion: String) {
    Column(verticalArrangement = Arrangement.spacedBy(ListItemDefaults.SegmentedGap)) {
        SegmentedListItem(
            shapes = ListItemDefaults.segmentedShapes(index = 0, count = 2),
            leadingContent = { Icon(painterResource(R.drawable.ic_info), contentDescription = null) },
            supportingContent = { Text(stringResource(R.string.settings_version, appVersion)) },
        ) {
            Text(stringResource(R.string.app_name))
        }
        SegmentedListItem(
            shapes = ListItemDefaults.segmentedShapes(index = 1, count = 2),
            leadingContent = { Icon(painterResource(R.drawable.ic_dns), contentDescription = null) },
            supportingContent = { Text(stringResource(R.string.settings_server_command)) },
        ) {
            Text(stringResource(R.string.settings_server_howto))
        }
    }
}
