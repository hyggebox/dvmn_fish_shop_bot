import os
import pathlib
from urllib.parse import urlsplit, unquote

import requests


def get_extension(url):
    split_url = urlsplit(unquote(url))
    file_extension = os.path.splitext(split_url.path)[1]
    return file_extension


def download_photo(headers, img_id):
    response = requests.get(f'https://api.moltin.com/v2/files/{img_id}',
                            headers=headers)
    response.raise_for_status()

    img_url = response.json()['data']['link']['href']
    ext = get_extension(img_url)
    product_img = pathlib.Path(f'images/{img_id}{ext}')
    if not product_img.exists():
        img_response = requests.get(img_url)
        img_response.raise_for_status()
        with open(product_img, 'wb') as file:
            file.write(img_response.content)


def get_capture_text(product_data):
    product_attrs = product_data['attributes']
    product_price = product_data['meta']['display_price']['without_tax']['formatted']
    product_description = product_attrs['description']

    text = f'{product_attrs["name"]}\n\nЦена: {product_price}/кг\n\n{product_description}'
    return text[:1024]