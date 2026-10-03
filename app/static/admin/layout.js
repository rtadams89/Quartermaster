// Phones get the mobile layout. "Use desktop layout" / "Use mobile layout" in the menu overrides this.
(function () {
  var pref = null;
  try { pref = localStorage.getItem('qm-layout'); } catch (e) { /* private mode */ }
  var phone = /Mobi|iPhone|iPod/i.test(navigator.userAgent) || (navigator.userAgentData && navigator.userAgentData.mobile === true);
  var mobile = pref === 'mobile' || (pref !== 'desktop' && (phone || window.innerWidth <= 700));
  document.documentElement.classList.toggle('mobile', mobile);
})();
