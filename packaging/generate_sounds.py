"""Reproduce the original synthesized task tones; no external audio assets."""
import math
import struct
import wave
from pathlib import Path

def generate(root):
    output=Path(root)/'assets/sounds';output.mkdir(parents=True,exist_ok=True)
    rate=22050
    for name,notes in [('completed',(660,880)),('error',(440,330,440))]:
        samples=[]
        for hz in notes:
            count=int(rate*.18)
            for i in range(count):
                envelope=min(1,i/(rate*.015),(count-i)/(rate*.035))
                samples.append(int(7500*envelope*math.sin(2*math.pi*hz*i/rate)))
            samples.extend([0]*int(rate*.08))
        with wave.open(str(output/f'{name}.wav'),'wb') as wav:
            wav.setparams((1,2,rate,0,'NONE','not compressed'))
            wav.writeframes(struct.pack('<'+'h'*len(samples),*samples))

if __name__=='__main__':generate(Path(__file__).resolve().parents[1])
