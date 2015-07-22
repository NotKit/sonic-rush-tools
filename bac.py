#!/usr/bin/python2
import argparse
import os.path
from itertools import izip_longest, product
from math import ceil
from struct import pack, unpack
from StringIO import StringIO
from wii_lz77 import WiiLZ77
from PIL import Image
from lz77 import compress

# Attributes from sprite.h of libnds
# Attribute 0 consists of 8 bits of Y plus the following flags:
attr_0_square  = (0 << 14)
attr_0_wide    = (1 << 14)
attr_0_tall    = (2 << 14)

# Atribute 1 consists of 9 bits of X plus the following flags:
attr_1_flip_x  = (1 << 12)
attr_1_flip_y  = (1 << 13)
attr_1_size_8  = (0 << 14)
attr_1_size_16 = (1 << 14)
attr_1_size_32 = (2 << 14)
attr_1_size_64 = (3 << 14)

size_square = [(8, 8), (16, 16), (32, 32), (64, 64)]
size_wide = [(16, 8), (32, 8), (32, 16), (64, 32)]
size_tall = [(8, 16), (8, 32), (16, 32), (32, 64)]

def bac_to_images(bac, debug=False):
    # Skip 4 bytes
    bac.seek(4)
    (animation_mappings_offset, animation_frames_offset, frame_assembly_offset, palette_offset,
        data_offset, info_offset) = unpack('<6i', bac.read(24))
        
    #print (animation_mappings_offset, animation_frames_offset, frame_assembly_offset, palette_offset,
        #data_offset, info_offset) 
        
    # Try to read animation frames    
    bac.seek(info_offset)
    block_size, frames_count, frames_info_size = unpack('<IHH', bac.read(8))
    #print block_size, frames_count, frames_info_size
    # Skip unknown values...
    unknown = unpack('<10h', bac.read(20))
    if debug:
        print unknown
    
    for i in range(frames_count):
        frame_info = unpack('<10h', bac.read(20))
        if debug:
            print frame_info
    
    bac.seek(animation_mappings_offset)
    block_size, = unpack('<I', bac.read(4))
    assert block_size == frames_count * 8 + 4
    
    animation_mappings = [unpack('<II', bac.read(8))[0] for i in range(frames_count)]
    if debug:
        print animation_mappings_offset, animation_mappings
    
    frames = []
    images = []
    parts = {}
    
    for frame_index, frame_offset in enumerate(animation_mappings):
        bac.seek(animation_frames_offset + frame_offset)
        
        # Frame assembly block
        block_id, block_size = unpack('<HH', bac.read(4))
        assert block_id == 1
        
        frame_assembly_parts_count = (block_size - 4) / 4
        assert frame_assembly_parts_count == 1
        frame_assembly_part_offset, = unpack('<I', bac.read(4))
        
        # Image parts
        block_id, block_size = unpack('<HH', bac.read(4))
        assert block_id == 2
        
        frame_parts_count = (block_size - 4) / 8
        # Image parts offsets in image data block, unknown value
        frame_parts_offsets = [bac.tell()]
        frame_parts = [list(unpack('<II', bac.read(8))) for i in range(frame_parts_count)]      
        
        # Palette parts
        block_id, block_size = unpack('<HH', bac.read(4))
        assert block_id == 3
        
        palette_parts_count = (block_size - 4) / 8
        assert palette_parts_count == 1
        
        # Palette offset in palettes block, unknown value
        palette_part = unpack('<II', bac.read(8))        

        # Read more parts with unknown meaning until we find terminating part with ID 04
        while bac.tell() < frame_assembly_offset:
            block_id, block_size = unpack('<HH', bac.read(4))
            if block_id == 2:
                frame_parts_offsets.append(bac.tell())
                
                subframe_parts_count = (block_size - 4) / 8
                subframe_parts = [list(unpack('<II', bac.read(8))) for i in range(subframe_parts_count)]                     
                
                try:
                    assert frame_parts == subframe_parts, "Frame {}: subframe parts differ, can't handle such images yet".format(frame_index)
                except AssertionError as e:
                    print e
            elif block_id == 4:
                break
            else:
                print block_id
                bac.seek(block_size - 4, 1)            
        
        palette_data = read_compressed(bac, palette_offset + palette_part[0])
        palette = load_palette(palette_data)
                
        # Get frame assembly info
        bac.seek(frame_assembly_offset + frame_assembly_part_offset)
        assert unpack('<I', bac.read(4))[0] == frame_parts_count
        frame_x, frame_y, frame_x_right, frame_y_bottom, hot_spot_x, hot_spot_y = unpack('<6h', bac.read(12))
        
        if debug:
            print 'Frame assembly at {}: frame_x: {}, frame_y: {}, frame_x_right: {}, frame_y_bottom: {}, hot_spot_x: {}, hot_spot_y: {}'.format(
                frame_assembly_offset + frame_assembly_part_offset, frame_x, frame_y, frame_x_right, frame_y_bottom, hot_spot_x, hot_spot_y)
        
        image_part_info = []        
        for i in range(frame_parts_count):
            image_part_info.append(unpack('<4H', bac.read(8)))
            
            if debug:
                print 'Image part at {}: attr_0: {}, attr_1: {}, attr_2: {}'.format(bac.tell() - 8, *image_part_info[-1][:3])
            
        #print image_part_coords
        
        frame_width = frame_x_right - frame_x
        frame_height = frame_y_bottom - frame_y
    
        frame_image = Image.new('P', (frame_width, frame_height))
        frame_image.putpalette(palette)
        
        # Now read image tiles and compose image part
        for part_index, part in enumerate(frame_parts):
            data = read_compressed(bac, data_offset + part[0])
            tiles = load_tiles(data, 1)
            
            attr_0, attr_1, attr_2 = image_part_info[part_index][:3]            
            
            part_y = attr_0 & 0xFF
            part_x = attr_1 & 0x1FF
            
            size = attr_1 >> 14
            
            if attr_0 & attr_0_tall:
                # Sprite shape is NxM with N < M (Height > Width)
                width, height = size_tall[size]
            elif attr_0 & attr_0_wide:
                # Sprite shape is NxM with N > M (Height < Width)
                width, height = size_wide[size]
            else:
                # Sprite shape is NxN (Height == Width)
                width, height = size_square[size]

            part.append((part_x, part_y, width, height))                                   
            tiles_per_row = width / 8
            
            #print image_part_info[part_index], part_y, part_x, size, width, height #width, len(tiles), tiles_per_row
            
            image = Image.new('P', (width, height))
            #image.putpalette(palette)
            
            for index, tile in enumerate(tiles):
                image.paste(tile, ((index % tiles_per_row) * 8, (index / tiles_per_row) * 8))

            frame_image.paste(image, (part_x, part_y))
            
        if debug:
            print ''
        
        frames.append((frame_width, frame_height, frame_parts, frame_parts_offsets))        
        images.append(frame_image)
            
    return images, frames
    
def read_compressed(bac, offset):
    # Decompress data
    bac.seek(offset)
    
    header = unpack("<I", bac.read(4))[0]    
    compression_type, uncompressed_length = header & 0xFF, header >> 8
            
    if compression_type == 0:
        # Uncompressed
        return bac.read(uncompressed_length)
    elif compression_type == 0x10:
        return WiiLZ77(bac, offset).uncompress()
    else:
        raise ValueError
        
def load_palette(palette_data):
    bgr_colors = list(izip_longest(*[iter(palette_data)] * 2))
    
    palette = []
    #transparency = None

    for color in bgr_colors:
        color = int(''.join(reversed(color)).encode('hex'), 16)
        color = (color & 31, (color & 31 << 5) >> 5, (color & 31 << 10) >> 10)
        red, green, blue = [int(n*(255 / 31.0)) for n in color]
        palette += [red, green, blue]
        
    #palette.extend([255, 0, 255]*(256-16))
        
    return palette
    
def load_tiles(data, color_format):
    # Load tiles
    tiles = []
    
    if color_format == 1:
        bytes_per_tile = 32
    else:
        bytes_per_tile = 64

    for tile_data in izip_longest(*[iter(data)] * bytes_per_tile):
        # Get color index for each pixel
        pixels = []
        for pixel in tile_data:
            value = ord(pixel)
            
            if color_format == 1:
                second = (value & 0xF0) >> 4
                first = value & 0x0F 
                pixels += [first, second]
            elif color_format == 2:
                pixels.append(value)

        # Create new image and load tile data
        tile = Image.new('P', (8, 8))
        tile.putdata(pixels)
        #tile.putpalette(temp_palette())
        tiles.append(tile)
        
    return tiles   

def image_to_tiles(image):
    tiles = [image.crop((x, y, x + 8, y + 8))
        for y, x in product(range(0, image.size[1], 8), range(0, image.size[0], 8))]
    return tiles

def tiles_to_data(image, color_format=1):
    data = []
    
    for tile in tiles:
        # Store two pixels of tile as one
        for first, second in izip_longest(*[iter(tile.getdata())] * 2):
            assert first < 16 and second < 16, (first, second)
            value = (second << 4) | first
            data.append(chr(value))
            
    return ''.join(data)

#                                                                                                                                      
# wrapper around PIL 1.1.6 Image.save to preserve PNG metadata
#
# public domain, Nick Galbreath                                                                                                        
# http://blog.modp.com/2007/08/python-pil-and-png-metadata-take-2.html                                                                 
#                                                                                                                                       
def pngsave(im, file):
    # these can be automatically added to Image.info dict                                                                              
    # they are not user-added metadata
    reserved = ('interlace', 'gamma', 'dpi', 'transparency', 'aspect')

    # undocumented class
    from PIL import PngImagePlugin
    meta = PngImagePlugin.PngInfo()
    # Provide our own _save function to properly save 4 bit images palette
    #Image.register_save("PNG", PngImagePlugin16._save)

    # copy metadata into new object
    for k,v in im.info.iteritems():
        if k in reserved: continue
        meta.add_text(k, v, 0)

    # and save
    im.save(file, "PNG", pnginfo=meta, transparency=0, bits=4)
        
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Rip bac from Sonic Rush to png(s) and import back')
    parser.add_argument('files', help='files to work with', type=argparse.FileType('r+b'), nargs="+")
    parser.add_argument('-u', '--update', metavar='frame', help='update frames\' parts from images', type=argparse.FileType('r+b'), nargs="+")
    parser.add_argument('-d', '--debug', action='store_true', help='debug mode')

    args = parser.parse_args()
    
    if not args.update:
        for input_file in args.files:    
            # Single image file
            try:
                images, frames = bac_to_images(input_file, args.debug)
            except Exception as e:
                print '{} while loading {}'.format(e, input_file.name)
                #raise
                continue
            
            for index, image in enumerate(images):
                bac_name = os.path.splitext(os.path.basename(input_file.name))[0]
                image_name = '{}_{}.png'.format(bac_name, index)

                # Use wrapper for preserving metadata
                with open(image_name, 'wb') as output:
                    pngsave(image, output)
    else:
        assert len(args.files) == 1, "Can't update more than one BAC file at once"
        bac = args.files[0]
        bac_name = os.path.splitext(os.path.basename(bac.name))[0]
        new_frames = args.update
        
        bac.seek(4)
        (animation_mappings_offset, animation_frames_offset, frame_assembly_offset, palette_offset,
            data_offset, info_offset) = unpack('<6i', bac.read(24))        
        
        images, frames = bac_to_images(bac, args.debug)
        parts_offsets = []
        
        for frame_width, frame_height, frame_parts, frame_parts_offset in frames:
            parts_offsets.extend([part[0] for part in frame_parts])
        parts_offsets = sorted(set(parts_offsets))    
            
        parts_data = [read_compressed(bac, data_offset + part) for part in parts_offsets]
        
        updated_parts = []           
        
        for index, (frame_width, frame_height, frame_parts, frame_parts_offset) in enumerate(frames):
            image_name = '{}_{}'.format(bac_name, index)
            
            for image in new_frames:
                if os.path.basename(os.path.splitext(image.name)[0]) == image_name:
                    image_name = image.name
                    break
            else:
                # Continue to next frame, leaving this one unchanged
                continue
            
            image = Image.open(image)
            
            # Recalculate color indexes to fit palette            
            data = list(image.getdata())
            # 0 color is alpha, so we have only 15 more left
            palette = list(izip_longest(*[iter(images[index].getpalette()[3:16*3])] * 3))
            image_palette = list(izip_longest(*[iter(image.getpalette())] * 3))
            
            try:
                image.info['transparency'] = ord(image.info['transparency'])
            except:
                pass
            
            # Hack for GIMP-saved GIF images
            if image_name.endswith('gif'):
                new_data = []
                for color in data:
                    if color == image.info['transparency']:
                        color = 0
                    else:
                        color += 1
                    new_data.append(color)
                    
                image.putdata(new_data)
                image_palette.insert(0, (0, 0, 0))
            
            # Check if image palettes match exactly and there are no indices higher than 15
            if image_palette[1:16] == palette:
                print 'Not changing color indices for {}'.format(image_name)
            else:
                new_data = []
                for color in data:
                    if color == image.info['transparency']:
                        new_data.append(0)
                    else:
                        try:
                            new_data.append(palette.index(image_palette[color]) + 1)
                        except ValueError:
                            print '{}: No such color in frame palette!'.format(image_name)
                            raise
                image.putdata(new_data)
                        
            for index, (offset, tiles_count, (part_x, part_y, width, height)) in enumerate(frame_parts):
                # Get part from image
                part = image.crop((part_x, part_y, part_x + width, part_y + height))
                # Convert part to tiles
                tiles = image_to_tiles(part)
                
                try:
                    assert len(tiles) == tiles_count, (len(tiles), tiles_count, (width, height))
                except AssertionError:
                    if args.debug:
                        print 'New tiles count is not equal to former one: {} vs {}, be careful'.format(len(tiles), tiles_count)
                        frame_parts[index][1] = len(tiles)
                    else:
                        raise
                
                # Update part's data with from new part
                data = tiles_to_data(tiles)
                
                # Check if we updated this part already, and if yes, reuse it only if new one is the same, otherwise store it as a separate
                if offset in updated_parts and parts_data[parts_offsets.index(offset)] != data:
                    print 'Adding new part for frame {}!'.format(index)
                    # Add new part...
                    offset = len(parts_data)
                    frame_parts[index][0] = offset
                    
                    parts_data.append(data)
                    parts_offsets.append(offset)
                    updated_parts.append(offset)
                else:
                    parts_data[parts_offsets.index(offset)] = data
                    updated_parts.append(offset)
            
        # Write new parts' data
        print parts_offsets
        bac.seek(data_offset + 4)
        bac.truncate()
        
        new_offsets = []
        
        for index, data in enumerate(parts_data):
            new_offsets.append(bac.tell() - data_offset)
            
            #bac.write(pack('<I', len(data) << 8))
            #bac.write(data)
            compress(data, bac)
            
            #try:
                #if bac.tell() - data_offset < parts_offsets[index + 1]:
                    #bac.seek(parts_offsets[index + 1] + data_offset)
            #except IndexError:
                #pass
            
            #while bac.tell() % 4:
            #    bac.write('\xff')
                
        print new_offsets
        
        # Calculate and write block size
        block_size = bac.tell() - data_offset
        bac.seek(data_offset)
        bac.write(pack('<I', block_size))
        
        # Update frame offsets
        for frame_width, frame_height, frame_parts, frame_parts_offsets in frames:
            for frame_parts_offset in frame_parts_offsets:
                bac.seek(frame_parts_offset)
                
                for offset, tiles_count, dimensions in frame_parts:
                    bac.write(pack('<II', new_offsets[parts_offsets.index(offset)], tiles_count))
