/* ===== Hyperagent: Markdown Renderer ===== */

function renderMarkdown(text) {
  // Escape HTML
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Code blocks (fenced)
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
    var label = lang ? '<span class="code-lang">' + lang + '</span>' : '';
    return '<div class="code-block">' + label + '<pre><code>' + code.trimEnd() + '</code></pre></div>';
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

  return html;
}
