/* ===== Hyperagent: Markdown Renderer ===== */

function renderMarkdown(text) {
  // Escape HTML
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Strip unicode emoji → ASCII/geometric equivalents (terminal aesthetic, no pictographs)
  html = html
    .replace(/\u2705/g, '\u25A0')   // ✅ → ■
    .replace(/\u274C/g, '\u25A1')   // ❌ → □
    .replace(/\u26A0\uFE0F?/g, '\u25B2') // ⚠️ → ▲
    .replace(/\u2714\uFE0F?/g, '\u25A0') // ✔️ → ■
    .replace(/\u2716\uFE0F?/g, '\u25A1') // ✖️ → □
    .replace(/\u2728/g, '\u25C6')   // ✨ → ◆
    .replace(/\u{1F680}/gu, '\u25B6')   // 🚀 → ▶
    .replace(/\u{1F4A1}/gu, '\u25C7')   // 💡 → ◇
    .replace(/\u{1F4DD}/gu, '\u25A3')   // 📝 → ▣
    .replace(/\u{1F527}/gu, '\u25E7')   // 🔧 → ◧
    .replace(/\u{1F6A8}/gu, '\u25B2')   // 🚨 → ▲
    .replace(/[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FA6F}\u{1FA70}-\u{1FAFF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{FE00}-\u{FE0F}\u{1F1E0}-\u{1F1FF}]/gu, '');

  // Code blocks (fenced) — stash in placeholders to protect from later transforms
  var codeBlocks = [];
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
    var label = lang ? '<span class="code-lang">' + lang + '</span>' : '';
    var body = code.trimEnd();
    var raw;
    try { raw = btoa(unescape(encodeURIComponent(body))); } catch (_e) { raw = ''; }
    var block = '<div class="code-block">' + label
      + '<button class="code-copy" data-code="' + raw + '">Copy</button>'
      + '<pre><code>' + body + '</code></pre></div>';
    var placeholder = '\x00CODEBLOCK' + codeBlocks.length + '\x00';
    codeBlocks.push(block);
    return placeholder;
  });

  // Inline code (protect from further transforms)
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Tables
  html = html.replace(/((?:^\|.+\|$\n?)+)/gm, function(tableBlock) {
    var rows = tableBlock.trim().split('\n');
    if (rows.length < 2) return tableBlock;
    var out = '<div class="table-wrap"><table>';
    var isHeader = true;
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i].trim();
      // Skip separator row (|---|---|)
      if (/^\|[\s\-:|]+\|$/.test(row)) { isHeader = false; continue; }
      var cells = row.split('|').slice(1, -1);
      var tag = isHeader ? 'th' : 'td';
      out += '<tr>';
      for (var j = 0; j < cells.length; j++) {
        out += '<' + tag + '>' + cells[j].trim() + '</' + tag + '>';
      }
      out += '</tr>';
      if (isHeader) isHeader = false;
    }
    out += '</table></div>';
    return out;
  });

  // Blockquotes
  html = html.replace(/(^&gt; .+(\n|$))+/gm, function(block) {
    var inner = block.replace(/^&gt; /gm, '');
    return '<blockquote>' + inner.trim() + '</blockquote>';
  });

  // Headers
  html = html.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

  // Bold and italic
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

  // Links
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');

  // Horizontal rules
  html = html.replace(/^---$/gm, '<hr>');

  // Ordered lists
  html = html.replace(/^(\d+)\. (.+)$/gm, '<li class="ol-item" value="$1">$2</li>');
  html = html.replace(/(<li class="ol-item"[^>]*>.*<\/li>\n?)+/g, '<ol>$&</ol>');

  // Unordered lists (only lines not already wrapped)
  html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li>(?!class).*<\/li>\n?)+/g, function(m) {
    if (m.indexOf('ol-item') > -1) return m;
    return '<ul>' + m + '</ul>';
  });

  // Paragraphs (double newlines)
  html = html.replace(/\n\n/g, '</p><p>');

  // ASCII emotes — wrap known emotes in glow span (skip if inside code/pre)
  var emotes = [
    '\\[\\+1\\]', '\\(-_-\\)b', '\\(\\._.\\)b', '\\[✓\\]',
    '\\\\o/',
    '\\(\\._.\\)', '\\(\\?_\\?\\)',
    '\\(￣\\^￣\\)ゞ',
    '\\(\\s+-_-\\)旦~',
    '\\(\\._\\. \\)'
  ];
  var emotePattern = new RegExp('(?<![\\w<])(' + emotes.join('|') + ')(?![\\w>])', 'g');
  html = html.replace(emotePattern, function(m) {
    return '<span class="emote">' + m + '</span>';
  });

  // Restore code blocks from placeholders
  for (var _i = 0; _i < codeBlocks.length; _i++) {
    html = html.replace('\x00CODEBLOCK' + _i + '\x00', codeBlocks[_i]);
  }

  return html;
}
