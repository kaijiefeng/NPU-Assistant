/** 运维界面展示名称；原始标识保留在详情中供排障。 */
export const JOB_LABELS = {
  ingest_upload: '上传资料编译',
  reindex: '重新编译文档',
  rebuild: '重建知识词条',
  scan_dir: '扫描目录收录',
  gap_suggest: '知识缺口找资料',
  subscription_check: '检查订阅更新',
  agent_prompt: '执行定时指令',
  ops_analyze: '分析运维故障',
  ops_execute: '执行运维修复',
};

export const CATEGORY_LABELS = {
  job: '后台任务', ops: '运行维护', agent: '管理助手', system: '系统服务',
  knowledge: '知识库', model: '模型服务', bot: '机器人', security: '安全',
};

const TOOL_LABELS = {
  import_url: '收录网页', import_text: '收录文本', import_batch: '批量收录',
  scan_dir: '扫描目录', reindex_document: '重新编译文档', delete_document: '删除源文件',
  rebuild_wiki: '重建知识库', update_wiki_page: '更新词条', delete_wiki_page: '删除词条',
  gap_suggest: '知识缺口找资料', gap_resolve: '解决知识缺口', gap_ignore: '忽略知识缺口',
  subscription_create: '添加订阅', subscription_check: '检查订阅', subscription_delete: '删除订阅',
  subscription_toggle: '切换订阅状态', ops_task_create: '创建故障分析', ops_task_approve: '批准运维修复',
  retry_job: '重试后台任务', cancel_job: '取消后台任务', ack_alert: '确认告警', resolve_alert: '解决告警',
};

export function jobLabel(type) {
  return JOB_LABELS[type] || '后台处理任务';
}

export function categoryLabel(category) {
  return CATEGORY_LABELS[category] || '其他事件';
}

export function alertTitle(title) {
  return String(title || '未命名告警').replace(/\b[a-z]+(?:_[a-z]+)+\b/g, (type) => JOB_LABELS[type] || type);
}

export function auditActionLabel(action) {
  const value = String(action || '');
  if (value.startsWith('agent.tool.')) return `助手 · ${TOOL_LABELS[value.slice(11)] || '管理操作'}`;
  const direct = {
    login: '登录账号', logout: '退出登录', 'auth.login': '登录账号', 'auth.logout': '退出登录',
    'user.create': '创建成员', 'user.update': '更新成员', 'user.delete': '删除成员',
    'auth.password': '修改密码', 'auth.password.reset': '重置密码', 'auth.setup': '初始化管理员',
    'auth.password_change': '修改密码', 'admin.user_create': '创建成员',
    'admin.user_update': '更新成员', 'admin.user_delete': '删除成员',
    'admin.password_reset': '重置成员密码', 'auth.login_failed': '登录失败',
  };
  if (direct[value]) return direct[value];
  const match = /^(POST|PUT|PATCH|DELETE) (\/api\/[^?]+)/.exec(value);
  if (!match) return /[\u3400-\u9fff]/.test(value) ? value : '管理操作';
  const [, method, path] = match;
  const verb = { POST: '提交', PUT: '更新', PATCH: '更新', DELETE: '删除' }[method];
  const specific = [
    [/\/retry$/, '重试任务'], [/\/cancel$/, '取消任务'], [/\/ack$/, '确认告警'],
    [/\/resolve$/, '标记已解决'], [/\/reindex$/, '重新编译文档'], [/\/rebuild$/, '重建词条'],
    [/\/run$/, '运行定时任务'], [/\/test$/, '测试连接'], [/\/default$/, '设为默认模型'],
    [/\/reset-password$/, '重置成员密码'], [/\/password$/, '修改密码'],
  ];
  for (const [pattern, label] of specific) if (pattern.test(path)) return label;
  const resources = [
    ['/agent/schedules', '定时任务'], ['/agent/conversations', '助手会话'], ['/agent/chat', '助手对话'],
    ['/knowledge/documents', '源文件'], ['/knowledge/import', '资料收录'], ['/knowledge/scan-dir', '目录扫描'],
    ['/wiki/pages', '知识词条'], ['/models/providers', '模型服务商'], ['/models', '模型配置'],
    ['/bots/channels', '机器人通道'], ['/admin/users', '团队成员'], ['/settings', '系统设置'],
    ['/insights', '服务台'], ['/subscriptions', '资料订阅'], ['/jobs', '后台任务'], ['/alerts', '系统告警'],
  ];
  const resource = resources.find(([part]) => path.includes(part));
  return resource ? `${verb}${resource[1]}` : `${verb}管理操作`;
}

export function auditResult(item) {
  const code = Number(item.status_code || 0);
  if (code >= 400) return { label: '失败', tone: 'red' };
  if (code >= 200 && code < 400) return { label: '成功', tone: 'green' };
  if (item.detail === 'ok') return { label: '成功', tone: 'green' };
  return { label: '已记录', tone: 'slate' };
}
