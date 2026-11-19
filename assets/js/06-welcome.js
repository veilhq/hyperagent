/* ===== Hyperagent: Welcome screen =====
   One builder for the empty-tab welcome: the centered brand mark.
   Used by the first tab (showWelcome) and by every
   tab created later (08-tabs.js).
*/

var WELCOME_MARK_SVG = '<svg class="welcome-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 37.03 35.95" fill="currentColor" aria-hidden="true"><path d="M19.97.02c-.5-.03-.92.39-.92.9v7.95c0,.47.37.84.84.89,4.16.42,7.42,3.95,7.42,8.23s-3.26,7.8-7.42,8.23c-.47.05-.84.41-.84.89v7.95c0,.5.42.92.92.9,9.49-.48,17.06-8.35,17.06-17.96S29.46.5,19.97.02Z"/><path d="M16.85,0H1.13C.51,0,0,.51,0,1.13v7.47c0,.62.51,1.13,1.13,1.13h15.72c.62,0,1.13-.51,1.13-1.13V1.13c0-.62-.51-1.13-1.13-1.13ZM7.51,6.72c0,.38-.3.68-.68.68h-3.7c-.37,0-.68-.3-.68-.68v-3.7c0-.37.3-.68.68-.68h3.7c.38,0,.68.3.68.68v3.7Z"/><path d="M17.21,26.22c-4.2-.39-7.5-3.93-7.5-8.23,0-2.51,1.13-4.77,2.91-6.28.32-.28.14-.8-.28-.8H2.25c-.47,0-.91.28-1.07.72-.75,1.98-1.17,4.12-1.17,6.36,0,9.61,7.57,17.48,17.06,17.96.5.03.92-.39.92-.9v-7.95c0-.45-.33-.84-.77-.88Z"/></svg>';

function buildWelcome() {
  var w = document.createElement('div');
  w.className = 'welcome';
  w.innerHTML = WELCOME_MARK_SVG;
  return w;
}
function showWelcome() {
  msgs.appendChild(buildWelcome());
}

// Show welcome on load if empty
showWelcome();
