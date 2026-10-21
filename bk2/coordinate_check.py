"""Offline native Maa coordinate check, shared by tests and portable self-test.

The custom controller has no access to Windows or the game; clicks are recorded
in a list. This tests Maa's real resize/mapping, not a reimplementation of it.
"""


def check_coordinates():
    import numpy as np
    from maa.controller import CustomController

    class Surface(CustomController):
        def connect(self):return True
        def request_uuid(self):return 'offline-coordinate-check'
        def get_features(self):return 0
        def screencap(self):return self.frame.copy()
        def click(self,x,y):self.clicks.append((x,y));return True

    controller=Surface()
    controller.clicks=[]
    controller.post_connection().wait()
    controller.set_screenshot_target_short_side(720)
    result=[]
    for width,height in ((1920,991),(1600,826),(1440,743),(1280,661),(1335,689)):
        controller.frame=np.full((height,width,3),80,dtype=np.uint8)
        image=controller.post_screencap().wait().get()
        sh,sw=image.shape[:2]
        if sh!=720 or abs(sw-width*720/height)>1:
            raise RuntimeError(f'Maa screenshot normalization mismatch: {width,height} -> {sw,sh}')
        errors=[]
        for x,y in ((0,0),(sw//2,sh//2),(sw-1,sh-1),(sw//4,sh*3//4)):
            controller.post_click(x,y).wait()
            px,py=controller.clicks[-1]
            error=max(abs(px-x*width/sw),abs(py-y*height/sh))
            if error>1.01 or not (0<=px<width and 0<=py<height):
                raise RuntimeError(f'Maa click mapping mismatch: {width,height}, {x,y} -> {px,py}')
            errors.append(error)
        result.append(dict(client=[width,height],screenshot=[sw,sh],max_error_pixels=max(errors)))
    return result
