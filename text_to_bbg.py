#!/usr/bin/python
import argparse
import csv
import os
from struct import pack
from PIL import Image, ImageDraw, ImageFont
import bb_bbg
import bbg

parser = argparse.ArgumentParser(description='Render translated text from csv file to Sonic Rush bb/bbg')
parser.add_argument('mode', help='output mode', choices=['talk_m', 'msg_c', 'msg_t'])
parser.add_argument('csv', help='csv text file', type=file)
parser.add_argument('font', help='font to use')
parser.add_argument('-p', '--png', help='store rendered text as PNGs for testing', action='store_true')
parser.add_argument('-b', '--bbg', help='dump created bbg', action='store_true')

args = parser.parse_args()

reader = csv.reader(args.csv)
font = ImageFont.load(args.font)

def draw_string(string, size, color, background, line_height, line_spacing, start=(0, 0)):
    image = Image.new('P', size, background)
    image.putpalette(bb_bbg.temp_palette())
    
    draw = ImageDraw.Draw(image)
    
    # We need to proccess each line separately to preserve manual breaks
    line_n = 0
    line_y = 0
    
    for line in string.splitlines():
        line_width = 0            
        words = []
        
        for word in line.split():
            # Add new word's width to current line's width
            line_width += font.getsize(word)[0] + 4
            
            # If becomes greater than available width, render current line and start new one with current word
            if line_width > size[0]:
                draw.text(((start[0], start[1] + line_y)), ' '.join(words), font=font, fill=color)
                
                line_n += 1
                line_y += line_height + line_spacing
                words = [word]
                line_width = font.getsize(word)[0]
            else:
                # Otherwise just append the word to current line's list
                words.append(word)
                
        # If we have any not rendered words yet, render them
        if words:
            draw.text((start[0], start[1] + line_y), ' '.join(words), font=font, fill=color)
            pass

        line_n += 1
        line_y += line_height + line_spacing    
    
    return image
  
bbgs = []    
    
for n, row in enumerate(reader):
    if len(row) > 1 and row[1]:
        string = row[1]
    else:
        string = row[0]

    # Work with font for Windows-CP1251, as PIL doen't support Unicode bitmap fonts
    string = string.decode('utf8').encode('cp1251')
    
    if args.mode == 'talk_m':
        background = 14
        color = 15
        line_height = 14
        line_spacing = 2
        size = (128, 56)
        vram_offset = 256
        start = (0, 4)
    elif args.mode in ('msg_c', 'msg_t'):
        background = 7
        color = 1
        line_height = 14
        line_spacing = 2
        size = (144, 48)
        start = (12, 8)
        vram_offset = 400
        
        if args.mode == 'msg_c':
            palette = 'yG-\x00\xb0\x0c3\x1d\xd6)Y:\xfcJ\x7fW\xd7<\x1c^^b=\x7f\xffs\xad5\xff\x7f\x00\x00'
        else:
            palette = 'yG)%\xaa1\x0cB\x8dN\x0f_\x90k\xf2w\x82I\x83n\xc3v\x95\x7f\xffs\xad5\xff\x7f\x00\x00'
            
        if n in range(15, 20):
            size = (144, 16)
            start = (12, 1)
    
    image = draw_string(string, size, color, background, line_height, line_spacing, start)    
            
    if args.png:
        image.save('{}_{:03}.png'.format(args.mode, n))
    elif args.mode in ('msg_c', 'msg_t'):
        output = open('eng{:02}.bbg'.format(n), 'wb')
        bbg_output = bbg.BBG(output, bbg.BBGHeader(0, 0, 0, 0, 1, 0, 0, 0xD0, 0))
        bbg_output.palette_data = palette
        bbg_output.update(image, vram_offset, compress=False)        
    else:
        bbg = bb_bbg.image_to_bbg(image, vram_offset, 14)
        bbgs.append(bbg)

if args.mode == 'talk_m':
    with open('talk_m_rus.bb', 'wb') as bb:
        bb.write('BB\x00\x00')
        # Write len of files
        bb.write(pack('<i', len(bbgs)))
        
        # Write files info and data
        offset = len(bbgs) * 8 + 8
        
        for n, bbg in enumerate(bbgs):
            # Write info
            bb.write(pack('<ii', offset, len(bbg)))
            next_entry = bb.tell()
            
            bb.seek(offset)
            bb.write(bbg)
            offset = bb.tell()
            
            bb.seek(next_entry)
            
            if args.bbg:
                with open('{}_{:03}.bbg'.format('talk_m_rus', n), 'wb') as output:
                    output.write(bbg)