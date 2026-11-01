from arc2dia2zen import bookmarks as B
from arc2dia2zen.transform import transform
from conftest import make_plan


def sample():
    root = B.BNode("Space <One> & Co")
    f = root.folder("Folder \"quoted\"", 1678307200)
    f.children.append(B.BNode("Tab <b>bold</b> & more", "https://ex.example.com/?a=1&b=<2>\"", 1678307300))
    sub = f.folder("Sub")
    sub.children.append(B.BNode("Deep", "https://deep.example.com/"))
    root.children.append(B.BNode("Loose", "https://loose.example.com/"))
    return [root]


def test_header_and_structure():
    html = B.render(sample())
    assert html.startswith("<!DOCTYPE NETSCAPE-Bookmark-file-1>")
    assert '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">' in html
    assert html.count("<DL><p>") == html.count("</DL><p>") == 4
    assert 'ADD_DATE="1678307200"' in html


def test_escaping():
    html = B.render(sample())
    assert "Space &lt;One&gt; &amp; Co" in html
    assert 'HREF="https://ex.example.com/?a=1&amp;b=&lt;2&gt;&quot;"' in html
    assert "<b>" not in html


def test_round_trip():
    nodes = B.parse(B.render(sample()))
    assert len(nodes) == 1 and nodes[0].title == "Space <One> & Co"
    links = [(p, n.title, n.url) for p, n in B.iter_links(nodes)]
    assert links == [
        (("Space <One> & Co", 'Folder "quoted"'), "Tab <b>bold</b> & more", 'https://ex.example.com/?a=1&b=<2>"'),
        (("Space <One> & Co", 'Folder "quoted"', "Sub"), "Deep", "https://deep.example.com/"),
        (("Space <One> & Co",), "Loose", "https://loose.example.com/"),
    ]
    assert nodes[0].children[0].add_date == 1678307200


def test_empty_folders_survive():
    root = B.BNode("S")
    root.folder("Empty")
    nodes = B.parse(B.render([root]))
    assert nodes[0].children[0].title == "Empty" and nodes[0].children[0].children == []


def test_exported_titles_and_dates(rich_sb):
    res = transform(rich_sb, make_plan(rich_sb, per_space={"Personal": {"folder_strategy": "to-bookmarks"}},
                                       folder_strategy="keep", dedupe_trees=False))
    nodes = B.parse(B.render(res.bookmarks))
    assert [n.title for n in nodes] == ["Personal"]
    soup = [n for _, n in B.iter_links(nodes) if n.url.endswith("/soup")]
    assert len(soup) == 2 and soup[0].title == 'Soup <& "quotes">'
    assert all(n.add_date and n.add_date > 1678307200 for _, n in B.iter_links(nodes))
    assert B.count_links(nodes) == 5
