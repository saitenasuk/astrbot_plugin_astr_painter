class Plain:
    def __init__(self, text=""):
        self.text = text

    def __repr__(self):
        return f"Plain({self.text!r})"


class Image:
    def __init__(self, path):
        self.path = path

    @staticmethod
    def fromFileSystem(path, **_):
        return Image(path)

    async def convert_to_file_path(self):
        return self.path

    def __repr__(self):
        return f"Image({self.path!r})"


class Reply:
    def __init__(self, chain=None):
        self.chain = chain or []
