"""Assemble docs/midas.html from the existing tracker page's shared pieces (style, nav) + page_body.html/page_script.js."""
import re
from pathlib import Path
R = Path(__file__).resolve().parents[1]
ix = (R / 'docs' / 'index.html').read_text()
here = Path(__file__).resolve().parent
head = ix[:ix.index('</style>')]
css_i = head.index('<style>')
nav_a = ix.index('<div class="site-nav">'); nav_b = ix.index('<div class="page">')
nav = ix[nav_a:nav_b]
navjs = ix[ix.rindex('<script>', 0, ix.index('shared site nav: "Analysis"')):ix.rindex('</script>') + 9]
LINK = '              <li><a href="https://michaeltkiley.github.io/gdpnow-replication/midas.html"%s>Real GDP Tracker: MIDAS</a></li>\n'
NAVM = nav.replace(' aria-current="page"', '')
NAVM = NAVM.replace('              <li><a href="https://michaeltkiley.github.io/gdpnow-replication/">Real GDP Tracker</a></li>\n',
                    '              <li><a href="https://michaeltkiley.github.io/gdpnow-replication/">Real GDP Tracker</a></li>\n' + LINK % ' aria-current="page"')
assert 'MIDAS' in NAVM or 'gdpnow-replication/">Real GDP Tracker</a></li>' in nav
if 'MIDAS' not in NAVM:
    NAVM = nav.replace('<a href="https://michaeltkiley.github.io/gdpnow-replication/" aria-current="page">Real GDP Tracker</a></li>\n',
                       '<a href="https://michaeltkiley.github.io/gdpnow-replication/">Real GDP Tracker</a></li>\n' + LINK % ' aria-current="page"')
head = head[:css_i]
meta = ('<style>\n' + ix[css_i + 7:ix.index('</style>')] + (here / 'page_extra.css').read_text() + '</style>\n</head>\n<body>\n')
head = re.sub(r'<title>.*?</title>', '<title>Real GDP Tracker: MIDAS — Michael T. Kiley</title>', head)
head = re.sub(r'(<meta name="description" content=")[^"]*', r'\1Daily MIDAS nowcasts of U.S. real GDP growth from monthly, weekly and daily indicators, refit in real time, with a comparison to GDPNow.', head)
head = re.sub(r'(og:title" content=")[^"]*', r'\1Real GDP Tracker: MIDAS', head)
head = re.sub(r'(og:description" content=")[^"]*', r'\1Daily MIDAS nowcasts of U.S. real GDP growth, refit in real time, compared with GDPNow.', head)
head = head.replace('gdpnow-replication/">', 'gdpnow-replication/midas.html">')
page = head + meta + NAVM + (here / 'page_body.html').read_text() + '\n<script>\n' + (here / 'page_script.js').read_text() + '\n</script>\n' + navjs + '\n</body>\n</html>\n'
(R / 'docs' / 'midas.html').write_text(page)
# add the same menu entry to the existing tracker page
if 'midas.html' not in ix:
    ix = ix.replace('<li><a href="https://michaeltkiley.github.io/gdpnow-replication/" aria-current="page">Real GDP Tracker</a></li>\n',
                    '<li><a href="https://michaeltkiley.github.io/gdpnow-replication/" aria-current="page">Real GDP Tracker</a></li>\n' + LINK % '')
    (R / 'docs' / 'index.html').write_text(ix)
print('wrote docs/midas.html', len(page))
