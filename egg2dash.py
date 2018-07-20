# -*- encoding: utf-8 -*-
import asyncio
import errno
import functools
import hashlib
import os.path
import queue
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import List
import urllib

import requests
import requests_html

CACHE_DIR = 'cache'
SAVE_DIR = 'eggjs.docset/Contents/Resources/Documents'
BASE_URL = 'https://eggjs.org/zh-cn/'
THREAD_POOL_SIZE = 8

THREADPOOL = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE)


def hash_url(url: str) -> str:
    if url.startswith(BASE_URL):
        return url[len(BASE_URL):]
    return hashlib.md5(url.encode('utf8')).hexdigest()


def cache_exists(url) -> bool:
    return os.path.isfile(os.path.join(CACHE_DIR, hash_url(url)))


def fetch_cache(url) -> str:
    with open(os.path.join(CACHE_DIR, hash_url(url)), 'r', encoding='utf8') as f:
        return f.read()


async def set_cache(url, content):
    hashed_url = hash_url(url)
    abs_path = os.path.join(CACHE_DIR, hashed_url)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    with open(abs_path, 'w', encoding='utf8') as f:
        return f.write(content)


async def save(url, content):
    hashed_url = hash_url(url)
    abs_path = os.path.join(SAVE_DIR, hashed_url)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    with open(abs_path, 'w', encoding='utf8') as f:
        return f.write(content)


async def get(url) -> str:
    if cache_exists(url):
        return fetch_cache(url)
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(THREADPOOL, functools.partial(requests.get, url))
    content = r.text
    await set_cache(url, content)
    return content


def filter_url(urls, visited) -> List[str]:
    return [url for url in urls if url.startswith(BASE_URL) and url.endswith('.html') and url not in visited]


async def job(url, find_queue, visited, semaphone):
    html = await get(url)
    print('Downloaded: ', url)
    html = requests_html.HTML(url=url, html=html)
    markdown = html.find('article.markdown-body', first=True).html
    title = html.find('title', first=True).text.replace(' - 为企业级框架和应用而生', '')
    await save(
        url, f"""
    <html>
        <head>
            <title>{ title }</title>
            <meta charset="UTF-8">
            <link rel="stylesheet" href="{ '../' if url[len(BASE_URL):].find('/') > 0 else ''  }index.css">
        </head>
        <body>
        { markdown }
        </body>
    </html>""")
    links = filter_url(html.absolute_links, visited)
    for link in links:
        visited.add(link)
        find_queue.put(link)
    semaphone.release()


async def fetch_doc_html():
    """递归下载所有HTML到本地"""
    find_queue = queue.Queue()
    visited = set()
    intro = 'https://eggjs.org/zh-cn/intro/index.html'
    semaphone = asyncio.Semaphore(THREAD_POOL_SIZE)
    tasks = []
    loop = asyncio.get_event_loop()

    find_queue.put(intro)

    while not find_queue.empty():
        while not find_queue.empty():
            await semaphone.acquire()
            next_url = find_queue.get()
            task = loop.create_task(job(next_url, find_queue, visited, semaphone))
            tasks.append(task)
        await asyncio.wait(tasks)


def insert_table(cursor, name, typ, path):
    cursor.execute("INSERT OR IGNORE INTO searchIndex(name, type, path) VALUES (?, ?, ?);", [name, typ, path])


def create_index():
    if os.path.exists(os.path.join(SAVE_DIR, os.path.pardir, 'docSet.dsidx')):
        os.remove(os.path.join(SAVE_DIR, os.path.pardir, 'docSet.dsidx'))
    conn = sqlite3.connect(os.path.join(SAVE_DIR, os.path.pardir, 'docSet.dsidx'))
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS searchIndex(id INTEGER PRIMARY KEY, name TEXT, type TEXT, path TEXT);')

    def iter_dir(dir_path):
        nonlocal c
        for pathname in os.listdir(dir_path):
            abs_path = os.path.join(dir_path, pathname)
            if pathname.endswith('html'):
                rel_path = os.path.relpath(abs_path, SAVE_DIR)
                with open(abs_path, 'r') as f:
                    html = requests_html.HTML(html=f.read())
                    title = html.find('title', first=True).text
                    insert_table(c, title, "Guide", rel_path)
                    print("Add guide: ", title, rel_path)

                    anchors = html.find('h2')
                    for i in anchors:
                        name = i.text[2:]
                        p = rel_path + '#' + urllib.parse.quote_plus(i.attrs['id'])
                        print("Add anchor: ", name, p)
                        insert_table(c, name, 'Section', p)

            elif os.path.isdir(abs_path):
                iter_dir(abs_path)

    iter_dir(SAVE_DIR)
    conn.commit()


async def download_css():
    url = 'https://eggjs.org/css/index.css'
    css = await get(url)
    with open(os.path.join(SAVE_DIR, 'index.css'), 'w') as f:
        f.write(css)
    print('Downloaded: ', url)


def post_generate():
    print('Generation complete.')


async def main():
    await fetch_doc_html()
    await download_css()
    create_index()
    post_generate()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
