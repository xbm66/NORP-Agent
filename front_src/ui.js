async function openSettings() {
    try { config = await window.pywebview.api.get_config(); } catch(e) { config = {}; }

    // ── Language selector (top of settings) ──
    var langSelect = document.getElementById('cfg-language');
    var savedLang = config.language || 'zh_CN';
    currentLang = savedLang;
    langSelect.value = savedLang;
    // Apply language change immediately on selection
    langSelect.addEventListener('change', function() {
        currentLang = this.value;
        setLanguage(this.value);
        // Update dynamic content that depends on language
        updateThinkMutualExclusion();
        updatePluginVisibility();
        renderPluginDirs();
        renderPluginList();
        renderSecurityAuditSummary();
        updateQueueWarning();
    });

    // ── 关闭按钮默认行为 ──
    document.getElementById('cfg-close-behavior').value = config.close_button_behavior || 'minimize_to_tray';

    document.getElementById('cfg-api-base').value = config.api_base || 'https://api.deepseek.com';
    document.getElementById('cfg-use-responses').checked = config.use_responses_api === true;
    document.getElementById('cfg-project-root').value = config.project_root || '';
    document.getElementById('cfg-queue-size').value = config.queue_max_size || 200;
    document.getElementById('cfg-max-steps').value = config.max_steps || 128;
    document.getElementById('cfg-task-timeout').value = config.task_timeout || 0;
    document.getElementById('cfg-web-search').checked = config.enable_web_search || false;
    document.getElementById('cfg-confirm-write-delete').checked = config.confirm_write_delete !== false;

    var thinkLevel = config.think_level || '高';
    document.getElementById('cfg-think-level').value = thinkLevel;
    var temp = config.temperature !== undefined ? config.temperature : 1.0;
    document.getElementById('cfg-temperature').value = Math.round(temp * 10);
    document.getElementById('cfg-temperature-value').textContent = temp.toFixed(1);
    document.getElementById('cfg-max-tokens').value = config.max_tokens || 32767;
    updateThinkMutualExclusion();

    document.getElementById('cfg-memory').checked = config.memory !== false;
    document.getElementById('cfg-memory-mode').value = config.memory_mode || 'full';
    document.getElementById('cfg-max-rounds').value = config.max_rounds || 10;

    // Plugin fields
    document.getElementById('cfg-plugins-enabled').checked = config.plugins_enabled !== false;
    updatePluginVisibility();
    renderPluginDirs();
    renderPluginList();

    // Security fields
    document.getElementById('cfg-security-audit').value = config.plugin_security_audit || 'warn';
    _prevSecurityAudit = document.getElementById("cfg-security-audit").value;
    document.getElementById('cfg-security-import-restrict').value = config.plugin_security_import_restrict || 'off';
    document.getElementById('cfg-security-permissions').checked = config.plugin_security_require_permissions || false;
    document.getElementById('cfg-security-resource-limit').checked = config.plugin_security_resource_limit || false;
    renderSecurityAuditSummary();

    // NORP safety system
    document.getElementById('cfg-norp-safe-enabled').checked = config.norp_safe_enabled !== false;
    updateNorpSafetyStatus();

    // Jailbreak guard
    document.getElementById('cfg-jailbreak-guard-enabled').checked = config.jailbreak_guard_enabled !== false;
    document.getElementById('cfg-jailbreak-guard-action').value = config.jailbreak_guard_action || 'block';
    updateJailbreakGuardStatus();

    // Custom system prompt
    document.getElementById('cfg-custom-prompt-enabled').checked = config.custom_system_prompt_enabled || false;
    document.getElementById('cfg-custom-prompt-source').value = config.custom_system_prompt_file ? 'file' : 'text';
    document.getElementById('cfg-custom-prompt').value = config.custom_system_prompt || '';
    document.getElementById('cfg-custom-prompt-file').value = config.custom_system_prompt_file || '';
    updateCustomPromptVisibility();
    updateCustomPromptSource();

    updateQueueWarning();
    setLanguage(currentLang);
    refreshRuntimeHealth();
    settingsModal.style.display = 'block';
    fetchModels(config.api_base || 'https://api.deepseek.com');
}

function updateThinkMutualExclusion() {
    var isThink = document.getElementById('cfg-think-level').value !== '关';
    var tempSlider = document.getElementById('cfg-temperature');
    tempSlider.disabled = isThink;
    var hint = document.getElementById('cfg-think-hint');
    if (hint) hint.style.display = isThink ? 'block' : 'none';
}

function updateQueueWarning() {
    var val = parseInt(document.getElementById('cfg-queue-size').value) || 200;
    document.getElementById('queue-warning').style.display = val > 300 ? 'block' : 'none';
}

// ── Plugin visibility toggle ──
function updatePluginVisibility() {
    var enabled = document.getElementById('cfg-plugins-enabled').checked;
    var pluginSection = document.getElementById('plugin-dirs-section');
    var pluginSecuritySection = document.getElementById('plugin-security-section');
    if (pluginSection) pluginSection.style.display = enabled ? 'block' : 'none';
    if (pluginSecuritySection) pluginSecuritySection.style.display = enabled ? 'block' : 'none';
}

// ── Custom system prompt visibility ──
function updateCustomPromptVisibility() {
    var enabled = document.getElementById('cfg-custom-prompt-enabled').checked;
    var section = document.getElementById('custom-prompt-section');
    if (section) section.style.display = enabled ? 'block' : 'none';
}

// ── Custom prompt source toggle (file vs text) ──
function updateCustomPromptSource() {
    var source = document.getElementById('cfg-custom-prompt-source').value;
    var textSection = document.getElementById('custom-prompt-text-section');
    var fileSection = document.getElementById('custom-prompt-file-section');
    if (textSection) textSection.style.display = source === 'text' ? 'block' : 'none';
    if (fileSection) fileSection.style.display = source === 'file' ? 'block' : 'none';
}

// ── Browse for custom prompt file ──
async function browseCustomPromptFile() {
    try {
        var path = await window.pywebview.api.pick_file('Markdown / Text (*.md;*.txt)');
        if (path) {
            document.getElementById('cfg-custom-prompt-file').value = path;
            // Read file content
            try {
                var content = await window.pywebview.api.read_text_file(path);
                if (content) {
                    document.getElementById('cfg-custom-prompt').value = content;
                }
            } catch(e) {
                // File path is set but content couldn't be auto-loaded
            }
        }
    } catch(e) {
        // Fallback: manual path entry
        var path = prompt('请输入提示词文件路径（.md / .txt）：');
        if (path) {
            document.getElementById('cfg-custom-prompt-file').value = path;
        }
    }
}

document.addEventListener('DOMContentLoaded', function() {
    var thinkSelect = document.getElementById('cfg-think-level');
    if (thinkSelect) {
        thinkSelect.addEventListener('change', updateThinkMutualExclusion);
    }

    var tempSlider = document.getElementById('cfg-temperature');
    if (tempSlider) {
        tempSlider.addEventListener('input', function() {
            document.getElementById('cfg-temperature-value').textContent = (this.value / 10).toFixed(1);
        });
    }

    var baseInput = document.getElementById('cfg-api-base');
    if (baseInput) {
        baseInput.addEventListener('change', function() {
            var url = baseInput.value.trim() || 'https://api.deepseek.com';
            fetchModels(url);
        });
    }
    document.getElementById('cfg-refresh-models').addEventListener('click', function() {
        var url = document.getElementById('cfg-api-base').value.trim() || 'https://api.deepseek.com';
        fetchModels(url);
    });

    // Plugins toggle → hide/show plugin-related settings
    var pluginsToggle = document.getElementById('cfg-plugins-enabled');
    if (pluginsToggle) {
        pluginsToggle.addEventListener('change', updatePluginVisibility);
    }

    // Custom system prompt toggle
    var customPromptToggle = document.getElementById('cfg-custom-prompt-enabled');
    if (customPromptToggle) {
        customPromptToggle.addEventListener('change', updateCustomPromptVisibility);
    }
    // Custom prompt source toggle
    var customPromptSource = document.getElementById('cfg-custom-prompt-source');
    if (customPromptSource) {
        customPromptSource.addEventListener('change', updateCustomPromptSource);
    }
    // Browse button for custom prompt file
    var browseBtn = document.getElementById('cfg-custom-prompt-browse');
    if (browseBtn) {
        browseBtn.addEventListener('click', browseCustomPromptFile);
    }
});

async function saveSettings() {
    var modelVal = document.getElementById('cfg-model').value;
    if (!modelVal || modelVal.trim() === '' || modelVal.trim() === '.') {
        showToast(t('select_valid_model'));
        return;
    }
    var newConfig = {
        language: document.getElementById('cfg-language').value,
        close_button_behavior: document.getElementById('cfg-close-behavior').value,
        model: modelVal.trim(),
        api_base: document.getElementById('cfg-api-base').value.trim(),
        use_responses_api: document.getElementById('cfg-use-responses').checked,
        project_root: document.getElementById('cfg-project-root').value.trim(),
        queue_max_size: parseInt(document.getElementById('cfg-queue-size').value) || 200,
        max_steps: parseInt(document.getElementById('cfg-max-steps').value) || 128,
        task_timeout: parseInt(document.getElementById('cfg-task-timeout').value) || 0,
        enable_web_search: document.getElementById('cfg-web-search').checked,
        confirm_write_delete: document.getElementById('cfg-confirm-write-delete').checked,
        think_level: document.getElementById('cfg-think-level').value,
        temperature: parseFloat(document.getElementById('cfg-temperature-value').textContent) || 1.0,
        max_tokens: parseInt(document.getElementById('cfg-max-tokens').value) || 32767,
        memory: document.getElementById('cfg-memory').checked,
        memory_mode: document.getElementById('cfg-memory-mode').value,
        max_rounds: parseInt(document.getElementById('cfg-max-rounds').value) || 10,
        plugins_enabled: document.getElementById('cfg-plugins-enabled').checked,
        plugin_dirs: config.plugin_dirs || [],
        plugin_security_audit: document.getElementById('cfg-security-audit').value,
        plugin_security_import_restrict: document.getElementById('cfg-security-import-restrict').value,
        plugin_security_require_permissions: document.getElementById('cfg-security-permissions').checked,
        plugin_security_resource_limit: document.getElementById('cfg-security-resource-limit').checked,
        custom_system_prompt_enabled: document.getElementById('cfg-custom-prompt-enabled').checked,
        custom_system_prompt: document.getElementById('cfg-custom-prompt').value,
        custom_system_prompt_file: document.getElementById('cfg-custom-prompt-file').value,
        norp_safe_enabled: document.getElementById('cfg-norp-safe-enabled').checked,
        jailbreak_guard_enabled: document.getElementById('cfg-jailbreak-guard-enabled').checked,
        jailbreak_guard_action: document.getElementById('cfg-jailbreak-guard-action').value,
    };

    try {
        await window.pywebview.api.save_config(newConfig);
        config = newConfig;
        currentLang = newConfig.language || 'zh_CN';
        settingsModal.style.display = 'none';
        showToast(t('settings_saved'));
    } catch(e) {
        showToast(t('save_failed') + ': ' + e.message);
    }
}

document.getElementById('browse-project-root').addEventListener('click', function() {
    window.pywebview.api.pick_directory().then(function(path) {
        if (path) document.getElementById('cfg-project-root').value = path;
    }).catch(function() {
        var path = prompt('Enter project directory path:');
        if (path) document.getElementById('cfg-project-root').value = path;
    });
});

async function restoreDefaults() {
    if (!confirm(t('restore_confirm'))) {
        return;
    }
    try {
        config = await window.pywebview.api.reset_config();
        document.getElementById('cfg-language').value = config.language || 'zh_CN';
        currentLang = config.language || 'zh_CN';
        setLanguage(currentLang);
        document.getElementById('cfg-close-behavior').value = config.close_button_behavior || 'minimize_to_tray';
        document.getElementById('cfg-api-base').value = config.api_base || 'https://api.deepseek.com';
        document.getElementById('cfg-use-responses').checked = config.use_responses_api === true;
        document.getElementById('cfg-project-root').value = config.project_root || '';
        document.getElementById('cfg-queue-size').value = config.queue_max_size || 200;
        document.getElementById('cfg-max-steps').value = config.max_steps || 128;
        document.getElementById('cfg-task-timeout').value = config.task_timeout || 0;
        document.getElementById('cfg-web-search').checked = config.enable_web_search || false;
        document.getElementById('cfg-confirm-write-delete').checked = config.confirm_write_delete !== false;
        document.getElementById('cfg-think-level').value = config.think_level || '高';
        var temp = config.temperature !== undefined ? config.temperature : 1.0;
        document.getElementById('cfg-temperature').value = Math.round(temp * 10);
        document.getElementById('cfg-temperature-value').textContent = temp.toFixed(1);
        document.getElementById('cfg-max-tokens').value = config.max_tokens || 32767;
        document.getElementById('cfg-memory').checked = config.memory !== false;
        document.getElementById('cfg-memory-mode').value = config.memory_mode || 'full';
        document.getElementById('cfg-max-rounds').value = config.max_rounds || 10;
        document.getElementById('cfg-plugins-enabled').checked = config.plugins_enabled !== false;
        updatePluginVisibility();
        config.plugin_dirs = config.plugin_dirs || [];
        renderPluginDirs();
        renderPluginList();

        document.getElementById('cfg-security-audit').value = config.plugin_security_audit || 'warn';
        document.getElementById('cfg-security-import-restrict').value = config.plugin_security_import_restrict || 'off';
        document.getElementById('cfg-security-permissions').checked = config.plugin_security_require_permissions || false;
        document.getElementById('cfg-security-resource-limit').checked = config.plugin_security_resource_limit || false;
        renderSecurityAuditSummary();

        // NORP safety system reset
        document.getElementById('cfg-norp-safe-enabled').checked = true;
        updateNorpSafetyStatus();

        // Jailbreak guard reset
        document.getElementById('cfg-jailbreak-guard-enabled').checked = true;
        document.getElementById('cfg-jailbreak-guard-action').value = 'block';
        updateJailbreakGuardStatus();

        // Custom system prompt reset
        document.getElementById('cfg-custom-prompt-enabled').checked = false;
        document.getElementById('cfg-custom-prompt').value = '';
        document.getElementById('cfg-custom-prompt-file').value = '';
        document.getElementById('cfg-custom-prompt-source').value = 'text';
        updateCustomPromptVisibility();
        updateCustomPromptSource();

        updateThinkMutualExclusion();
        updateQueueWarning();
        ensureModelOptions(document.getElementById('cfg-model'), config.model);
        showToast(t('restored'));
    } catch(e) {
        showToast(t('save_failed') + ': ' + e.message);
    }
}

// Plugin management UI

function renderPluginDirs() {
    var list = document.getElementById('plugin-dirs-list');
    var dirs = config.plugin_dirs || [];
    if (dirs.length === 0) {
        list.innerHTML = '<span style="font-size:11px;color:#888;">' + t('no_plugin_dirs') + '</span>';
        return;
    }
    var html = '';
    for (var i = 0; i < dirs.length; i++) {
        html += '<div style="display:flex;align-items:center;gap:4px;margin-bottom:3px;font-size:12px;">';
        html += '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;background:#f0f0f0;padding:2px 6px;border-radius:3px;">' + escapeHtml(dirs[i]) + '</span>';
        html += '<button class="btn-small" style="padding:1px 6px;font-size:10px;" onclick="removePluginDir(' + i + ')">x</button>';
        html += '</div>';
    }
    list.innerHTML = html;
}

async function renderPluginList() {
    var list = document.getElementById('plugin-list');
    try {
        var plugins = await window.pywebview.api.get_plugins();
        if (!plugins || plugins.length === 0) {
            list.innerHTML = '<span style="font-size:11px;color:#888;">' + t('no_plugins_found') + '</span>';
            renderSecurityAuditSummary();
            return;
        }
        var html = '';
        for (var i = 0; i < plugins.length; i++) {
            var p = plugins[i];
            var icon = p.enabled ? '✅' : '⚠️';
            var err = p.error ? ' <span style="color:#dc3545;">(' + escapeHtml(p.error) + ')</span>' : '';

            // Security audit badges
            var auditBadge = '';
            if (p.audit_critical > 0) {
                auditBadge += ' <span style="background:#dc3545;color:#fff;padding:0 4px;border-radius:2px;font-size:10px;">🔴' + p.audit_critical + '</span>';
            }
            if (p.audit_warning > 0) {
                auditBadge += ' <span style="background:#e08f3a;color:#fff;padding:0 4px;border-radius:2px;font-size:10px;">🟠' + p.audit_warning + '</span>';
            }

            html += '<div style="padding:3px 0;border-bottom:1px solid #eee;">';
            html += icon + ' <b>' + escapeHtml(p.name) + '</b> v' + escapeHtml(p.version);
            if (p.publisher) html += ' <span style="font-size:10px;color:#888;">by ' + escapeHtml(p.publisher) + '</span>';
            html += ' — tools:' + p.tool_count + ' hooks:' + p.hook_count;
            html += auditBadge;
            if (p.description) html += '<br><span style="font-size:10px;color:#888;">' + escapeHtml(p.description) + '</span>';
            html += err;
            html += '</div>';
        }
        list.innerHTML = html;
        renderSecurityAuditSummary();
    } catch(e) {
        list.innerHTML = '<span style="color:#dc3545;">Failed to load plugins: ' + escapeHtml(e.message || e) + '</span>';
    }
}

async function renderSecurityAuditSummary() {
    var el = document.getElementById('plugin-security-audit-summary');
    try {
        var plugins = await window.pywebview.api.get_plugins();
        if (!plugins || plugins.length === 0) {
            el.style.display = 'none';
            return;
        }
        var totalCritical = 0, totalWarning = 0, totalInfo = 0;
        var blockedPlugins = [];
        for (var i = 0; i < plugins.length; i++) {
            var p = plugins[i];
            if (p.audit_critical) totalCritical += p.audit_critical;
            if (p.audit_warning) totalWarning += p.audit_warning;
            if (p.audit_info) totalInfo += p.audit_info;
            if (!p.enabled && p.error && p.error.indexOf('Security audit blocked') >= 0) {
                blockedPlugins.push(p.name);
            }
        }

        if (totalCritical === 0 && totalWarning === 0 && totalInfo === 0 && blockedPlugins.length === 0) {
            el.style.display = 'none';
            return;
        }

        el.style.display = 'block';
        var html = '<b>' + t('audit_summary_label') + ':</b> ';
        var parts = [];
        if (totalCritical > 0) parts.push('<span style="color:#dc3545;">🔴 ' + totalCritical + ' ' + t('audit_critical') + '</span>');
        if (totalWarning > 0) parts.push('<span style="color:#e08f3a;">🟠 ' + totalWarning + ' ' + t('audit_warning') + '</span>');
        if (totalInfo > 0) parts.push('ℹ️ ' + totalInfo + ' ' + t('audit_info'));
        html += parts.join(', ') || 'No issues found';

        if (blockedPlugins.length > 0) {
            html += '<br><span style="color:#dc3545;">' + t('audit_blocked') + ': ' + blockedPlugins.join(', ') + '</span>';
            html += '<br><span style="font-size:10px;color:#888;">' + t('audit_blocked_hint') + '</span>';
            // Add to message center (deduplicated by plugin name)
            for (var b = 0; b < blockedPlugins.length; b++) {
                var alreadyExists = false;
                for (var m = 0; m < messageCenter.length; m++) {
                    if (messageCenter[m].type === 'plugin' && messageCenter[m].detail.indexOf(blockedPlugins[b]) !== -1) {
                        alreadyExists = true;
                        break;
                    }
                }
                if (!alreadyExists) {
                    addToMessageCenter('plugin', t('audit_blocked'), blockedPlugins[b], 'Plugins');
                }
            }
        }
        el.innerHTML = html;
    } catch(e) {
        el.style.display = 'none';
    }
}

// ── NORP 安全系统 UI ──

function updateNorpSafetyStatus() {
    var checkbox = document.getElementById('cfg-norp-safe-enabled');
    var statusEl = document.getElementById('norp-safety-status');

    if (!checkbox || !statusEl) return;

    if (checkbox.checked) {
        statusEl.style.background = '#e8f5e9';
        statusEl.style.color = '#2e7d32';
        statusEl.setAttribute('data-i18n', 'norp_safety_enabled_status');
        statusEl.textContent = t('norp_safety_enabled_status');
    } else {
        statusEl.style.background = '#fff3e0';
        statusEl.style.color = '#e65100';
        statusEl.setAttribute('data-i18n', 'norp_safety_disabled_status');
        statusEl.textContent = t('norp_safety_disabled_status');
    }
    // Also update the top warning bar
    updateNorpSafetyWarningBar();
}

// ── 顶部 NORP 安全警告条 ──
function updateNorpSafetyWarningBar() {
    var bar = document.getElementById('norp-safety-warning-bar');
    if (!bar) return;
    // Always sync i18n text before showing, in case language was switched
    var key = bar.getAttribute('data-i18n');
    if (key) {
        var val = t(key, currentLang);
        if (/<[a-zA-Z][^>]*>/.test(val)) {
            bar.innerHTML = val;
        } else {
            bar.textContent = val;
        }
    }
    var checkbox = document.getElementById('cfg-norp-safe-enabled');
    if (checkbox) {
        // 优先使用 checkbox 当前状态（反映用户最新操作）
        bar.style.display = checkbox.checked ? 'none' : 'block';
        return;
    }
    // fallback: 从 config 读取
    bar.style.display = (config.norp_safe_enabled !== false) ? 'none' : 'block';
}

// NORP safety toggle: custom warning modal (like security-off-warning-modal)
document.addEventListener('DOMContentLoaded', function() {
    var checkbox = document.getElementById('cfg-norp-safe-enabled');
    if (checkbox) {
        checkbox.addEventListener('change', function() {
            if (!this.checked) {
                // Show custom warning modal instead of confirm()
                this.checked = true; // revert for now
                norpSafetyOffWarningModal.style.display = 'block';
            } else {
                config.norp_safe_enabled = true;
                updateNorpSafetyStatus();
                updateNorpSafetyWarningBar();
                try {
                    window.pywebview.api.set_norp_safe_enabled(true);
                } catch(e) {}
            }
        });
    }
});

// NORP safety off modal buttons
document.addEventListener('DOMContentLoaded', function() {
    var confirmBtn = document.getElementById('norp-safety-off-confirm-btn');
    var cancelBtn = document.getElementById('norp-safety-off-cancel-btn');
    if (confirmBtn) {
        confirmBtn.addEventListener('click', function() {
            var cb = document.getElementById('cfg-norp-safe-enabled');
            if (cb) cb.checked = false;
            config.norp_safe_enabled = false;
            norpSafetyOffWarningModal.style.display = 'none';
            updateNorpSafetyStatus();
            updateNorpSafetyWarningBar();
            try {
                window.pywebview.api.set_norp_safe_enabled(false);
            } catch(e) {}
        });
    }
    if (cancelBtn) {
        cancelBtn.addEventListener('click', function() {
            norpSafetyOffWarningModal.style.display = 'none';
            // Checkbox was already reverted to checked in the change handler
        });
    }
});

// ── 越狱防护状态更新 ──
function updateJailbreakGuardStatus() {
    var statusEl = document.getElementById('jailbreak-guard-status');
    var actionEl = document.getElementById('cfg-jailbreak-guard-action');
    var enabled = document.getElementById('cfg-jailbreak-guard-enabled').checked;
    if (!statusEl) return;
    if (enabled) {
        statusEl.style.background = '#e8f5e9';
        statusEl.style.color = '#2e7d32';
        statusEl.setAttribute('data-i18n', 'jailbreak_guard_enabled_status');
        statusEl.textContent = t('jailbreak_guard_enabled_status');
        if (actionEl) actionEl.disabled = false;
    } else {
        statusEl.style.background = '#fff3e0';
        statusEl.style.color = '#e65100';
        statusEl.setAttribute('data-i18n', 'jailbreak_guard_disabled_status');
        statusEl.textContent = t('jailbreak_guard_disabled_status');
        if (actionEl) actionEl.disabled = true;
    }
}

// Jailbreak guard toggle
document.addEventListener('DOMContentLoaded', function() {
    var checkbox = document.getElementById('cfg-jailbreak-guard-enabled');
    if (checkbox) {
        checkbox.addEventListener('change', function() {
            updateJailbreakGuardStatus();
        });
    }
});

async function addPluginDir() {
    try {
        var path = prompt('请输入插件目录路径:');
        if (!path) return;
        await window.pywebview.api.add_plugin_dir(path);
        renderPluginDirs();
    } catch(e) {
        showToast('添加插件目录失败: ' + e.message);
    }
}

// ═══════════════════════════════════════════════════════════════
//  运行时健康检查
// ═══════════════════════════════════════════════════════════════

async function refreshRuntimeHealth() {
    var statusEl = document.getElementById('runtime-health-status');
    var textEl = document.getElementById('runtime-health-text');
    var detailEl = document.getElementById('runtime-health-detail');

    if (!statusEl || !textEl) return;

    try {
        var health = await window.pywebview.api.get_runtime_health();
        if (!health) {
            textEl.textContent = t('runtime_health_no_data');
            statusEl.style.background = '#f0f0f0';
            return;
        }

        var overallHealthy = health.overall_healthy;
        var fatalCount = health.fatal_count || 0;
        var errorCount = health.error_count || 0;
        var warningCount = health.warning_count || 0;
        var envType = health.environment_type || 'unknown';

        // ── 状态栏 ──
        var icon, bgColor, statusText;
        if (fatalCount > 0) {
            icon = '🔴';
            bgColor = '#fce4e4';
            statusText = t('runtime_health_fatal', null).replace('{count}', fatalCount);
        } else if (errorCount > 0) {
            icon = '🟠';
            bgColor = '#fff3e0';
            statusText = t('runtime_health_error', null).replace('{count}', errorCount);
        } else if (warningCount > 0) {
            icon = '🟡';
            bgColor = '#fffde7';
            statusText = t('runtime_health_warning', null).replace('{count}', warningCount);
        } else {
            icon = '✅';
            bgColor = '#e8f5e9';
            statusText = t('runtime_health_ok');
        }

        // 附加环境类型
        var envLabels = {
            'windows_sandbox': t('runtime_env_sandbox'),
            'docker': t('runtime_env_docker'),
            'vm': t('runtime_env_vm'),
            'wine': t('runtime_env_wine'),
            'normal': '',
            'unknown': ''
        };
        statusText += (envLabels[envType] || '');

        statusEl.style.background = bgColor;
        statusEl.querySelector('span').textContent = icon;
        textEl.textContent = statusText;

        // ── 详细信息 ──
        var checks = health.checks || [];
        var detailHtml = '';
        for (var i = 0; i < checks.length; i++) {
            var c = checks[i];
            var sevIcon = {fatal: '🔴', error: '🟠', warning: '🟡', info: 'ℹ️'}[c.severity] || '•';
            var passedIcon = c.passed ? '✓' : '✗';
            var color = c.passed ? '#2e7d32' : (c.severity === 'fatal' ? '#c62828' : c.severity === 'error' ? '#e65100' : '#f57f17');
            detailHtml += '<div style="color:' + color + ';margin-bottom:4px;">';
            detailHtml += sevIcon + ' <b>' + escHtml(c.name) + '</b> ' + passedIcon;
            detailHtml += '<br><span style="color:#666;">' + escHtml(c.message) + '</span>';
            if (c.detail) {
                detailHtml += '<br><span style="color:#888;font-size:10px;">' + escHtml(c.detail).replace(/\n/g, '<br>') + '</span>';
            }
            if (c.suggestion) {
                detailHtml += '<br><span style="color:#1565c0;font-size:10px;">💡 ' + escHtml(c.suggestion) + '</span>';
            }
            detailHtml += '</div>';
        }
        detailEl.innerHTML = detailHtml;

        // 根据 toggle 按钮状态决定是否展开详情
        var toggleBtn = document.getElementById('runtime-health-toggle');
        if (toggleBtn && toggleBtn.textContent === '▼') {
            detailEl.style.display = 'block';
        }

    } catch(e) {
        textEl.textContent = t('runtime_health_failed').replace('{error}', e.message);
        statusEl.style.background = '#f0f0f0';
    }
}

function escHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function browsePluginDir() {
    try {
        var path = await window.pywebview.api.pick_plugin_dir();
        if (!path) return;
        await window.pywebview.api.add_plugin_dir(path);
        config.plugin_dirs = await window.pywebview.api.get_plugin_dirs();
        renderPluginDirs();
        renderPluginList();
        showToast(t('plugin_dir_added'));
    } catch(e) {
        showToast(t('save_failed') + ': ' + (e.message || e));
    }
}

async function removePluginDir(index) {
    var dirs = config.plugin_dirs || [];
    if (index < 0 || index >= dirs.length) return;
    var path = dirs[index];
    try {
        await window.pywebview.api.remove_plugin_dir(path);
        config.plugin_dirs = await window.pywebview.api.get_plugin_dirs();
        renderPluginDirs();
        renderPluginList();
        showToast(t('plugin_dir_removed'));
    } catch(e) {
        showToast(t('save_failed') + ': ' + (e.message || e));
    }
}

async function reloadPlugins() {
    try {
        await window.pywebview.api.reload_plugins();
        config.plugin_dirs = await window.pywebview.api.get_plugin_dirs();
        renderPluginDirs();
        renderPluginList();
        showToast(t('plugins_reloaded'));
    } catch(e) {
        showToast(t('save_failed') + ': ' + (e.message || e));
    }
}

// Attach plugin event handlers when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    var addBtn = document.getElementById('add-plugin-dir');
    if (addBtn) addBtn.addEventListener('click', addPluginDir);
    var reloadBtn = document.getElementById('reload-plugins-btn');
    if (reloadBtn) reloadBtn.addEventListener('click', reloadPlugins);
});

function showApiKeyModal() {
    document.getElementById('apikey-input').value = '';
    apikeyModal.style.display = 'block';
    document.getElementById('apikey-input').focus();
}

async function confirmApiKey() {
    var key = document.getElementById('apikey-input').value.trim();
    if (!key) return;
    try {
        var result = await window.pywebview.api.set_api_key(key);
        if (result === 'ok') {
            apikeyModal.style.display = 'none';
            showToast(t('apikey_updated'));
        } else {
            showToast(t('invalid_apikey'));
            document.getElementById('apikey-input').value = '';
            document.getElementById('apikey-input').focus();
        }
    } catch(e) { showToast(t('error_prefix') + ': ' + e.message); }
}

async function showBalance() {
    try {
        var data = await window.pywebview.api.get_balance();
        if (data.error) {
            showToast(t('balance_query_failed') + ': ' + data.error);
        } else if (data.balance_infos && data.balance_infos.length > 0) {
            var info = data.balance_infos[0];
            var msg = t('balance_format').replace('{total}', info.total_balance).replace('{granted}', info.granted_balance).replace('{topped}', info.topped_up_balance);
            alert(msg);
        } else {
            showToast(t('balance_unavailable'));
        }
    } catch(e) { showToast(t('balance_query_failed') + ': ' + e.message); }
}

sendBtn.addEventListener('click', handleSend);
stopBtn.addEventListener('click', handleStop);
document.getElementById('key-btn').addEventListener('click', showApiKeyModal);
document.getElementById('settings-btn').addEventListener('click', openSettings);
document.getElementById('settings-save-btn').addEventListener('click', saveSettings);
document.getElementById('runtime-health-refresh').addEventListener('click', refreshRuntimeHealth);
document.getElementById('runtime-health-toggle').addEventListener('click', function() {
    var detailEl = document.getElementById('runtime-health-detail');
    var isHidden = detailEl.style.display === 'none' || !detailEl.style.display;
    if (isHidden) {
        detailEl.style.display = 'block';
        this.textContent = '▼';
        this.title = t('runtime_health_toggle_collapse');
    } else {
        detailEl.style.display = 'none';
        this.textContent = '▶';
        this.title = t('runtime_health_toggle_expand');
    }
});

// ── Message Center ──
document.getElementById('msg-center-btn').addEventListener('click', function() {
    var modal = document.getElementById('message-center-modal');
    if (modal.style.display === 'block') {
        modal.style.display = 'none';
    } else {
        renderMessageCenter();
        modal.style.display = 'block';
        updateMsgBadge();
    }
});
document.getElementById('msg-center-close').addEventListener('click', function() {
    document.getElementById('message-center-modal').style.display = 'none';
});
document.getElementById('msg-center-clear-all').addEventListener('click', function() {
    clearAllMessages();
});

// Security audit off warning

var _prevSecurityAudit = document.getElementById("cfg-security-audit").value;
document.getElementById("cfg-security-audit").addEventListener("change", function() {
    var newVal = this.value;
    if (newVal === "off" && _prevSecurityAudit !== "off") {
        securityOffWarningModal.style.display = "block";
        this.value = _prevSecurityAudit;
    } else {
        _prevSecurityAudit = newVal;
    }
});
document.getElementById("security-off-confirm-btn").addEventListener("click", function() {
    document.getElementById("cfg-security-audit").value = "off";
    _prevSecurityAudit = "off";
    securityOffWarningModal.style.display = "none";
});
document.getElementById("security-off-cancel-btn").addEventListener("click", function() {
    securityOffWarningModal.style.display = "none";
});
document.getElementById('settings-cancel').addEventListener('click', function() { settingsModal.style.display = 'none'; });
document.getElementById('settings-restore-defaults').addEventListener('click', restoreDefaults);

document.getElementById('clear-memory-btn').addEventListener('click', async function() {
    if (!confirm('Clear all saved conversation history? This cannot be undone.')) {
        return;
    }
    try {
        var tab = getActiveTab();
        var sid = tab ? tab.dbId : '';
        var result = await window.pywebview.api.clear_memory(sid);
        if (result) {
            document.getElementById('memory-status').textContent = t('memory_cleared');
            showToast(t('memory_cleared'));
        } else {
            document.getElementById('memory-status').textContent = t('no_memory');
            showToast(t('no_memory'));
        }
    } catch(e) {
        showToast(t('save_failed') + ': ' + e.message);
    }
});
document.getElementById('cfg-queue-size').addEventListener('input', updateQueueWarning);
document.getElementById('balance-btn').addEventListener('click', showBalance);
document.getElementById('about-btn').addEventListener('click', function() { aboutModal.style.display = 'block'; });
document.getElementById('about-close-btn').addEventListener('click', function() { aboutModal.style.display = 'none'; });

document.getElementById('apikey-confirm-btn').addEventListener('click', confirmApiKey);
document.getElementById('apikey-cancel-btn').addEventListener('click', function() { apikeyModal.style.display = 'none'; });
document.getElementById('apikey-get-btn').addEventListener('click', function() {
    openExternal('https://platform.deepseek.com/');
});

userInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.ctrlKey && !e.shiftKey) {
        e.preventDefault();
        handleSend();
    } else if (e.key === 'Enter' && (e.ctrlKey || e.shiftKey)) {
        e.preventDefault();
        insertNewlineAtCursor();
    }
});
userInput.addEventListener('input', autoResizeTextarea);

apikeyModal.addEventListener('click', function(e) { if (e.target === apikeyModal) apikeyModal.style.display = 'none'; });
aboutModal.addEventListener('click', function(e) { if (e.target === aboutModal) aboutModal.style.display = 'none'; });

function checkCDN() {
    var missing = [];
    if (typeof marked === 'undefined') missing.push('marked');
    if (typeof katex === 'undefined') missing.push('katex');
    if (missing.length > 0) {
        reloadCDN();
    }
}

function reloadCDN() {
    if (typeof marked === 'undefined') {
        var script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/marked/marked.min.js';
        document.head.appendChild(script);
    }
    if (typeof katex === 'undefined') {
        if (!document.querySelector('link[href*="katex.min.css"]')) {
            var link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = 'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css';
            document.head.appendChild(link);
        }
        var script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js';
        document.head.appendChild(script);
    }
}

setInterval(function() {
    if (navigator.onLine) {
        if (typeof marked === 'undefined' || typeof katex === 'undefined') {
            checkCDN();
        }
    }
}, 3000);

setTimeout(function() {
    if (navigator.onLine) {
        if (typeof marked === 'undefined' || typeof katex === 'undefined') {
            checkCDN();
        }
    }
}, 1000);