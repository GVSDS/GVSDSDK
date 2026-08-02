from django.http import HttpResponse


def GetDefaultAvatar():
    from io import BytesIO
    from PIL import Image
    img = Image.new('RGB', (256, 256), (200, 200, 200))
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return HttpResponse(buf.read(), content_type='image/png')
