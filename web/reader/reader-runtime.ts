(() => {
  const marker = 'data-reader-runtime';
  if (document.documentElement.hasAttribute(marker)) {
    return;
  }

  document.documentElement.setAttribute(marker, 'loaded');
})();
