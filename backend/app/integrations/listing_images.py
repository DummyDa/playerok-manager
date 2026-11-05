"""Reduce large listing covers before multipart upload."""
from io import BytesIO
from PIL import Image,ImageOps


def optimize(image,filename):
    if len(image)<=512*1024:return image,filename
    try:
        with Image.open(BytesIO(image)) as source:
            if source.width*source.height>40_000_000:raise ValueError("Слишком большое разрешение обложки")
            alpha="A" in source.getbands() or "transparency" in source.info
            cover=ImageOps.exif_transpose(source)
            cover.thumbnail((1600,1600),Image.Resampling.LANCZOS)
            output=BytesIO()
            if alpha:
                cover.convert("RGBA").save(output,"PNG",optimize=True)
                name="listing.png"
            else:
                cover.convert("RGB").save(output,"JPEG",quality=90,optimize=True)
                name="listing.jpg"
            result=output.getvalue()
            return (result,name) if len(result)<len(image) else (image,filename)
    except (OSError,Image.DecompressionBombError) as exc:
        raise ValueError("Не удалось прочитать изображение объявления") from exc
