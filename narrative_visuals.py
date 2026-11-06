"""Artifact-first source panels over session-driven art; no external effects."""
from __future__ import annotations
from PIL import Image, ImageDraw, ImageFont, ImageOps
try:
    from .narrative import excerpt_catalog, caption_beats
except ImportError:
    from narrative import excerpt_catalog, caption_beats

URL = 'github.com/thedavidmurray/hermes-demo-reel'
LABELS = {'ask':'USER ASK · source excerpt', 'file_input':'CODE · recorded file input',
          'patch_input':'DIFF · recorded patch input','terminal_input':'COMMAND · recorded input',
          'tool_output':'RESULT · recorded tool output'}


def _wrap(draw,text,font,width):
    lines=[]
    for paragraph in text.splitlines() or ['']:
        current=''
        for word in paragraph.split():
            trial=(current+' '+word).strip()
            if draw.textlength(trial,font=font)>width and current:
                lines.append(current); current=word
            else: current=trial
        lines.append(current)
    return lines


def _center(draw,text,font,width,y,color):
    for line in _wrap(draw,text,font,width*.82):
        draw.text((width/2,y),line,font=font,anchor='mt',fill=color)
        y+=font.size*1.2
    return y


def paint_narrative_frames(paths,snapshot,board,title_path):
    """Before hashing/freezing, composite only approved copy and frozen excerpts.

    No path from a historical session is opened. paths/title_path are local,
    already-confined render assets. Returned frame names/order remain unchanged.
    """
    entries=excerpt_catalog(snapshot)
    by_event={}
    for entry in entries: by_event.setdefault(entry['event_id'],[]).append(entry)
    with Image.open(paths[0]) as image: width,height=image.size
    base_size=min(width,height)
    heading=ImageFont.load_default(size=round(base_size*.070))
    caption_font=ImageFont.load_default(size=round(base_size*.045))
    label_font=ImageFont.load_default(size=round(base_size*.027))
    code_font=ImageFont.load_default(size=round(base_size*.031))
    foot_font=ImageFont.load_default(size=round(base_size*.025))
    overlays=[]
    for index,scene in enumerate(board.scenes):
        choices=by_event[scene.event_ids[0]]
        # Inputs and output have the same event ID; show actual output on final.
        excerpt=next((e for e in choices if e['kind']=='tool_output'),choices[0]) if index==len(board.scenes)-1 else choices[0]
        act='01 / THE ASK' if index==0 else '03 / THE RESULT' if index==len(board.scenes)-1 else '02 / THE WORK'
        for start,end,caption in caption_beats(scene):
            panel=Image.new('RGBA',(width,height),(7,16,28,212))
            d=ImageDraw.Draw(panel)
            d.text((width*.08,height*.065),'DEMO REEL  /  '+act,font=label_font,fill='#6BE0C3')
            headline_font=heading
            while len(_wrap(d,scene.title,headline_font,width*.82))*headline_font.size*1.2 > height*.14 and headline_font.size>18:
                headline_font=ImageFont.load_default(size=headline_font.size-1)
            _center(d,scene.title,headline_font,width,height*.14,'#F2F7FA')
            _center(d,caption,caption_font,width,height*.31,'#D6E8EF')
            top,bottom=round(height*.45),round(height*.79)
            d.rounded_rectangle((width*.08,top,width*.92,bottom),radius=16,fill=(12,29,43,255),outline='#326672',width=2)
            d.text((width*.105,top+height*.022),LABELS[excerpt['kind']],font=label_font,fill='#6BE0C3')
            y=top+height*.082
            lines=_wrap(d,excerpt['text'],code_font,width*.78)
            max_lines=max(1,int((bottom-y-height*.035)/(code_font.size*1.28)))
            for line in lines[:max_lines]:
                d.text((width*.105,y),line,font=code_font,fill='#E3EFF4'); y+=code_font.size*1.28
            if len(lines)>max_lines:
                d.text((width*.105,bottom-height*.032),'… excerpt truncated; full text in approval preview',font=foot_font,fill='#82A2B1')
            if index==len(board.scenes)-1:
                _center(d,'Demo Reel · sessions → shareable video',label_font,width,height*.805,'#6BE0C3')
                _center(d,URL,foot_font,width,height*.855,'#D6E8EF')
            else:
                _center(d,'Source excerpt, not a live screen recording',foot_font,width,height*.83,'#82A2B1')
            overlays.append((start,end,panel))
    with Image.open(title_path) as title_image:
        generated_bg=ImageOps.colorize(title_image.convert('L').resize((width,height)), '#07101c','#326672').convert('RGBA')
    fps=24
    pointer=0
    for i,path in enumerate(paths):
        moment=i*1000//fps
        while pointer<len(overlays)-1 and moment>=overlays[pointer][1]: pointer+=1
        with Image.open(path) as im:
            frame=im.convert('RGBA')
        frame=Image.blend(frame,generated_bg,.08)
        frame=Image.alpha_composite(frame,overlays[pointer][2])
        d=ImageDraw.Draw(frame)
        # Legible event-mapped progress with a legend, not unexplained orbit dots.
        y=height*.935
        d.line((width*.08,y,width*.92,y),fill='#326672',width=3)
        source_time=(snapshot.events[-1].t_ms if snapshot.events else 0)*moment/(board.options.duration_s*1000)
        total=max(1,snapshot.events[-1].t_ms)
        for e in snapshot.events:
            x=width*(.08+.84*e.t_ms/total)
            color='#6BE0C3' if e.t_ms<=source_time else '#486576'
            d.ellipse((x-4,y-4,x+4,y+4),fill=color)
        d.text((width*.08,height*.965),'SOURCE EVENTS · filled = played · compressed timeline',font=foot_font,fill='#82A2B1',anchor='ls')
        frame.convert('RGB').save(path)
        frame.close()
    generated_bg.close()
    for _,_,panel in overlays: panel.close()
