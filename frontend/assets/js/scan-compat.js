/** 扫描功能的版本兼容：旧服务仍可使用既有扫描提交接口。 */
import { getScanDirectory, getSettings } from './api.js';

export function scanErrorMessage(error) {
  if (error?.status === 404) return '当前后端尚未加载此扫描接口，请重启 NPU-Assistant 后端后重试。资料路径不存在时会返回具体目录错误，不能通过修改路径解决这个 404。';
  return error?.message || '扫描操作失败，请稍后重试';
}

export async function loadScanDirectory() {
  try { return await getScanDirectory(); }
  catch (error) {
    if (error?.status !== 404) throw error;
    // 仅在接口缺失时降级；鉴权/网络错误不吞掉，也不从前端推断服务器路径。
    let saved = '';
    try { saved = String((await getSettings())?.knowledge_scan_dir || ''); }
    catch { /* 无设置读取权限时仍可显式输入目录并提交既有扫描接口。 */ }
    return {
      legacy: true, accessible: true, saved_path: saved, path: saved,
      source: saved ? 'saved' : 'default', path_scope: 'server',
    };
  }
}
