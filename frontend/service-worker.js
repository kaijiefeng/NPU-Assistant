/**
 * service-worker.js — NPU-Assistant PWA 静态壳缓存
 *
 * 策略：
 *  - 安装时预缓存静态壳（HTML/CSS/JS 模块/图标/manifest），版本化缓存名；
 *  - `/api/` 请求一律 network-only（绝不缓存业务数据，保证管理操作实时）；
 *  - 页面导航 network-first，断网回退缓存的 index.html；
 *  - 静态资源 network-first 并写入缓存（在线始终最新，离线可用）；
 *  - 激活时清理旧版本缓存。
 *
 * 说明：本文件由 index.html 通过 app.js 在 https 或 localhost 下注册。
 */
const CACHE_VERSION = 'npu-assistant-static-v3';
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/assets/styles.css',
  '/assets/js/app.js',
  '/assets/js/api.js',
  '/assets/js/ui.js',
  '/assets/js/mobile-access.js',
  '/assets/js/views/chat.js',
  '/assets/js/views/knowledge.js',
  '/assets/js/views/models.js',
  '/assets/js/views/bots.js',
  '/assets/js/views/settings.js',
  '/assets/icons/icon-192.png',
  '/assets/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_VERSION)
      .then((cache) =>
        // 单个资源 404 不应导致整个安装失败（图标可能尚未生成）
        Promise.all(
          STATIC_ASSETS.map((url) =>
            cache.add(url).catch(() => {
              /* 忽略单个资源失败 */
            }),
          ),
        ),
      )
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith('npu-assistant-') && key !== CACHE_VERSION)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // 业务接口：network-only，绝不缓存
  if (url.pathname.startsWith('/api/')) return;

  // 页面导航：network-first（no-cache 条件请求，部署后立即拿到新版本），断网回退 index.html
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request, { cache: 'no-cache' })
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put('/index.html', copy)).catch(() => {});
          return response;
        })
        .catch(() =>
          caches
            .match('/index.html')
            .then((cached) => cached || caches.match('/'))
            .then(
              (cached) =>
                cached ||
                new Response('NPU-Assistant 暂时离线，请联网后重试。', {
                  status: 503,
                  headers: { 'Content-Type': 'text/plain; charset=utf-8' },
                }),
            ),
        ),
    );
    return;
  }

  // 静态资源：network-first（no-cache 条件请求，避免部署后仍加载旧 JS/CSS），断网回退缓存
  event.respondWith(
    fetch(request, { cache: 'no-cache' })
      .then((response) => {
        if (response && response.ok) {
          const copy = response.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put(request, copy)).catch(() => {});
        }
        return response;
      })
      .catch(() =>
        caches.match(request).then(
          (cached) =>
            cached ||
            new Response('资源暂时不可用，请联网后重试。', {
              status: 503,
              headers: { 'Content-Type': 'text/plain; charset=utf-8' },
            }),
        ),
      ),
  );
});
