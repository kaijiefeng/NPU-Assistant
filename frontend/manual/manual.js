/* NPU-Assistant 手册 / 产品页交互：滚动进度、导航状态、目录高亮、进场动画 */
(function () {
  var root = document.documentElement;
  root.classList.add('js');

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var topbar = document.getElementById('topbar');
  var progress = document.querySelector('.progress span');
  var toTop = document.getElementById('toTop');

  var ticking = false;
  function onScroll() {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(function () {
      var y = window.scrollY || document.documentElement.scrollTop;
      if (topbar) topbar.classList.toggle('is-scrolled', y > 24);
      if (toTop) toTop.classList.toggle('show', y > 640);
      if (progress) {
        var max = document.documentElement.scrollHeight - window.innerHeight;
        progress.style.width = (max > 0 ? Math.min(100, (y / max) * 100) : 0) + '%';
      }
      ticking = false;
    });
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll);
  onScroll();

  if (toTop) {
    toTop.addEventListener('click', function () {
      window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' });
    });
  }

  // 进场动画
  var revealTargets = document.querySelectorAll(
    '.hero-copy, .hero-visual, .hero-demo, section, .mini, .card, .kpi, figure, .feature, .cta'
  );
  if (!reduceMotion && 'IntersectionObserver' in window) {
    var revealObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-in');
          revealObserver.unobserve(entry.target);
        }
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.06 });

    revealTargets.forEach(function (el, i) {
      // 首屏元素不做位移，避免闪烁
      if (el.closest('.hero') || el.classList.contains('hero-demo')) return;
      el.setAttribute('data-reveal', '');
      el.style.transitionDelay = Math.min(i % 6, 5) * 40 + 'ms';
      revealObserver.observe(el);
    });
  }

  // 目录高亮：按滚动位置选择最后一个越过顶部的条目
  var tocLinks = Array.prototype.slice.call(document.querySelectorAll('.toc a[href^="#"]'));
  var tocTargets = tocLinks
    .map(function (a) { return { link: a, el: document.getElementById(a.getAttribute('href').slice(1)) }; })
    .filter(function (t) { return t.el; });

  function updateToc() {
    if (!tocTargets.length) return;
    var active = null;
    for (var i = 0; i < tocTargets.length; i++) {
      if (tocTargets[i].el.getBoundingClientRect().top <= 160) active = tocTargets[i].link;
    }
    tocLinks.forEach(function (a) {
      a.classList.toggle('active', a === active);
    });
  }
  var tocTick = false;
  window.addEventListener('scroll', function () {
    if (tocTick) return;
    tocTick = true;
    window.requestAnimationFrame(function () { updateToc(); tocTick = false; });
  }, { passive: true });
  updateToc();

  // 平滑锚点（兼容不支持 scroll-behavior 的浏览器）
  document.querySelectorAll('a[href^="#"]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      var id = a.getAttribute('href');
      if (id.length < 2) return;
      var target = document.querySelector(id);
      if (!target) return;
      e.preventDefault();
      target.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
      if (history.replaceState) history.replaceState(null, '', id);
    });
  });
})();
