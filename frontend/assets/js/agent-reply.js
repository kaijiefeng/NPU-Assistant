/** 仅兼容完整的助手回复外壳；普通文本与 JSON 示例保持原样。 */
export function containsAgentProtocol(value) {
  return /<\s*\/?\s*[|｜]+\s*DSML\b/i.test(String(value || ''));
}
export function containsModelNoise(value) {
  return /<\/?\s*ds_safety\b|^\s*\[用户未成年\]/i.test(String(value || '').trim());
}
export function normalizeAgentReply(value) {
  let current = value;
  for (let depth = 0; depth < 5; depth += 1) {
    if (current && typeof current === 'object' && !Array.isArray(current)) {
      const keys = Object.keys(current);
      const key = ['reply', 'replay'].find((name) => Object.hasOwn(current, name));
      if (!key || !keys.every((name) => ['reply', 'replay', 'steps', 'conversation_id', 'conversation_title'].includes(name))) return String(value || '');
      current = current[key];
      continue;
    }
    if (typeof current !== 'string') return String(value || '');
    const text = current.trim();
    if (containsAgentProtocol(text)) return '这条历史回复包含未识别的工具调用标记，未完成有效回答。请重新提问；不会因打开历史而执行工具。';
    if (containsModelNoise(text)) return '这条历史回复包含模型内部标记，未形成有效回答。请重新提问。';
    const candidate = text.replace(/^```(?:json)?\s*\n?([\s\S]*?)\n?```\s*$/i, '$1').trim();
    let data;
    try { data = JSON.parse(candidate); }
    catch {
      const match = /^\{\s*["“”](reply|replay)["“”]\s*[:：]\s*(["“”])([\s\S]*)["“”]\s*\}$/.exec(candidate);
      if (!match || /[,，]\s*["“”][\w-]+["“”]\s*[:：]/.test(match[3])) return text;
      let inner = match[3];
      if (match[2] === '"') {
        try { inner = JSON.parse(`"${inner.replace(/(?<!\\)"/g, '\\"')}"`); } catch { /* 保留正文 */ }
      }
      data = { [match[1]]: inner };
    }
    if (typeof data === 'string' && /^\{\s*["“”](reply|replay)["“”]\s*[:：]/.test(data.trim())) { current = data; continue; }
    if (data && typeof data === 'object' && !Array.isArray(data)) {
      const key = ['reply', 'replay'].find((name) => Object.hasOwn(data, name));
      if (key && Object.keys(data).every((name) => ['reply', 'replay', 'steps', 'conversation_id', 'conversation_title'].includes(name))) { current = data[key]; continue; }
    }
    return text;
  }
  return '这条回复格式不完整，请重试。';
}
