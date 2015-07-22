#!/usr/bin/python2
import argparse
import os.path
from collections import namedtuple
from itertools import izip_longest
from math import ceil
from struct import pack, unpack
from StringIO import StringIO
from PIL import Image
from wii_lz77 import WiiLZ77
import lz77

BBGHeader = namedtuple('BBGHeader', ('size', 'data_offset', 'mappings_offset', 'palette_offset',
            'color_format', 'row_len', 'rows_n', 'bbg_palette_index', 'unknown'))

class BBG(object):
    """A class for converting BBG image into PNG and importing it back"""
    def __init__(self, bbg, header=None):
        self.bbg = bbg
        self.header = header
        
        if not self.header:
            # Skip 4 bytes
            assert bbg.read(4) == 'BBG\00'
            self.header = header = BBGHeader(*unpack('<5i4h', bbg.read(28)))
            
            self.data = read_compressed(bbg, header.data_offset)
            self.mappings_data = read_compressed(bbg, header.mappings_offset)
            self.palette_data = read_compressed(bbg, header.palette_offset)

    def to_image(self, bbg_palette=None):
        bbg = self.bbg
        (size, data_offset, mappings_offset, palette_offset,
            color_format, row_len, rows_n, bbg_palette_index, unknown) = self.header
            
        data = self.data # read_compressed(bbg, data_offset)
            
        # color_format: 1 - 4 bpp, 2 - 8 bpp
        if color_format == 1:
            bytes_per_tile = 32
        elif color_format == 2:
            bytes_per_tile = 64
        elif color_format == 5:
            print 'Unknown color format, assuming 8 bpp'
            bytes_per_tile = 64
            color_format = 2
        else:
            with open('dump.bin', 'wb') as test:
                test.write(data)
            assert False, 'Unknown color format!'
            
        if data_offset == mappings_offset == palette_offset:
            'Warning, palette only data detected!'

        tiles_n = len(data) / bytes_per_tile
        print('Tiles count: {}'.format(tiles_n)) 
        
        # Load tiles
        tiles = []
        temp_i = 0
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
            temp_i += 1
            
        # Load mappings
        mappings_data = self.mappings_data # read_compressed(bbg, mappings_offset)
        # Use value of first tile as offset, 4 bits of first byte are palette index
        self.vram_offset = offset = unpack('<H', mappings_data[0:2])[0] & 0x03FF - 1
        
        mappings = []

        for tile in izip_longest(*[iter(mappings_data)] * 2):
            value = unpack('<H', tile[0] + tile[1])[0]
            palette_index = value >> 12
            vram_n = value & 0x03FF
            n = vram_n - offset
            flip_h = bool(value & 0x400)
            flip_v = bool(value & 0x800)
            mappings.append((n, flip_h, flip_v, palette_index))
            
        # Load palette
        if bbg_palette:
            palette_data = BBG(bbg_palette).palette_data
        else:    
            palette_data = self.palette_data
        
        palette_index = bbg_palette_index >> 4
        print 'VRAM offset, palette_index: {} {}'.format(offset, palette_index)
        
        if palette_data:
            bgr_colors = list(izip_longest(*[iter(palette_data)] * 2))
            
            palette = []
            #transparency = None

            for color in bgr_colors:
                color = int(''.join(reversed(color)).encode('hex'), 16)
                color = (color & 31, (color & 31 << 5) >> 5, (color & 31 << 10) >> 10)
                red, green, blue = [int(n*(255 / 31.0)) for n in color]
                palette += [red, green, blue]
                #if [red, green, blue] == [248, 0, 248] and transparency == None:
                #    transparency = (len(palette)/3)-1
        else:
            palette = temp_palette()
        
        # Create new image to draw mappings
        result = Image.new('P', (row_len * 8, rows_n * 8))
        result.putpalette(palette)
        if palette_index:
            result.info["palette_index"] = str(palette_index)
            result.info["palette_count"] = str(len(palette) / 48)
        
        rows = list(izip_longest(*[iter(mappings)] * row_len))

        # Put tiles to image as specified in mappings
        for row in range(0, len(rows)):
            for tile in range(0, len(rows[row])):
                entry = rows[row][tile]
                
                if entry:
                    try:
                        image = tiles[entry[0]]
                    except IndexError:
                        continue
                    
                    # Flip horizontally
                    if entry[1]:
                        image = image.transpose(Image.FLIP_LEFT_RIGHT)
                    # Flip vertically
                    if entry[2]:
                        image = image.transpose(Image.FLIP_TOP_BOTTOM)
                    
                    # Try to fix colors for images with more than one palette based on palette index
                    if palette_data and palette_index < entry[3]:
                        # Calculate offset of tile's palette in result one
                        tile_palette_offset = (entry[3] - palette_index) * 16
                        pixels = [pixel + tile_palette_offset for pixel in image.getdata()]
                        
                        image = Image.new('P', (8, 8))
                        image.putdata(pixels)
                        
                    result.paste(image, (tile * 8, row * 8))
        
        return result
        
    def update(self, image, vram_offset=0, empty=False, compress=True):
        if not vram_offset:
            vram_offset = getattr(self, 'vram_offset', 0)

        # First tile - empty tile?
        if empty:
            empty = [0] * (8 * 8)
            tiles = [empty]    
        else:
            tiles = []
        
        mappings = []    
        reused = 0    
        
        transforms = (
            (None, False, False),
            (Image.FLIP_LEFT_RIGHT, True, False),
            (Image.FLIP_TOP_BOTTOM, False, True),
            (Image.ROTATE_180, True, True),
        )
        
        # Iterate over top-left coordinates of each tile and split image to 8x8 tiles
        for y in range(0, image.size[1], 8):
            for x in range(0, image.size[0], 8):
                # Crop image to 8x8 tile
                tile = image.crop((x, y, x + 8, y + 8))
                
                # Try to find tile duplicates, fliping horizontally/vertically/both if necessary
                for method, flip_h, flip_v in transforms:
                    if method:
                        transposed = tile.transpose(method)
                    else:
                        transposed = tile
                        
                    data = list(transposed.getdata())
                    
                    # Try to tile duplicate
                    try:
                        n = tiles.index(data)
                    except ValueError:
                        pass
                    else:
                        mappings.append((n, flip_h, flip_v))
                        reused += 1
                        break
                    
                else:
                    data = list(tile.getdata())
                    n = len(tiles)
                    
                    tiles.append(data)
                    mappings.append((n, False, False))
                    
        #assert reused + len(tiles) == len(mappings) + 1   
        print '{} tiles reused, {} in result'.format(reused, len(tiles), len(mappings))    
        
        # Now try to update bbg file itself
        bbg = self.bbg        
        (size, data_offset, mappings_offset, palette_offset,
            color_format, row_len, rows_n, bbg_palette_index, unknown) = self.header
            
        # Write tile data
        data_offset = 0x20
        bbg.seek(data_offset)
        
        # FIXME: Need to implement LZ77 compression
        if color_format == 1:
            data_length = len(tiles) * (8 * 8 / 2)
        else:
            data_length = len(tiles) * (8 * 8)
            
        
        data = []
        palette_indexes = []
        
        for tile in tiles:
            if color_format == 1:
                # Hack for 4 bpp images with multiple palettes
                palette_index = tile[0] // 16
                palette_indexes.append(palette_index)                
                # Store two pixels of tile as one
                for first, second in izip_longest(*[iter(tile)] * 2):
                    first %= 16
                    second %= 16
                    value = (second << 4) | first
                    data.append(chr(value))
            else:
                data += map(chr, tile)
        
        assert len(data) == data_length        

        if compress:
            lz77.compress(data, bbg)
        else:
            bbg.write(pack('<i', data_length << 8))
            bbg.write(''.join(data))
        
        # Write mappings    
        mappings_offset = bbg.tell()    
        
        mappings_length = len(mappings) * 2
        bbg.write(pack('<i', mappings_length << 8))
        
        palette_index = bbg_palette_index >> 4
        mappings_data = []
        
        for n, flip_h, flip_v in mappings:
            # Calculate vram tile offset
            value = n + vram_offset
            # Add palette index
            #value |= bbg_palette_index << 8# 12
            value |= (palette_index + palette_indexes[n]) << 12
            # Set flip_h and flip_v flags
            value |= flip_h << 10
            value |= flip_v << 11
            
            # Pack value
            value = pack('<H', value)
            mappings_data.append(value)
            
        assert len(mappings_data) * 2 == mappings_length
        bbg.write(''.join(mappings_data))

        # Write palette
        palette_offset = bbg.tell()
        if self.palette_data:
            bbg.write(pack('<i', len(self.palette_data) << 8))
            bbg.write(self.palette_data)
        else:
            bbg.write('\x00' * 4)
        
        # Write header
        size = bbg.tell()
        bbg.seek(0)
        bbg.write('BBG\00')
        
        row_len = image.size[0] / 8
        rows_n = image.size[1] / 8
        
        self.header = BBGHeader(size, data_offset, mappings_offset, palette_offset,
            color_format, row_len, rows_n, bbg_palette_index, unknown)
        
        #unknown = 1
        
        #bbg.write(pack('<5i2h', size, data_offset, mappings_offset, palette_offset, unknown, row_len, rows_n))  
        bbg.write(pack('<5i4h', *self.header))

class BB(object):
    """A class for working with multiple BBGs stored in one BB file"""
    def __init__(self, bb, contents=[]):
        self.contents = contents[:]
        
        if not self.contents:        
            # Read num of files
            bb.seek(0x4)
            n_files = unpack('<i', bb.read(4))[0]
            
            # Extract contents
            for n in range(n_files):
                offset, size = unpack('<ii', bb.read(8))
                current = bb.tell()
                
                # Read file's data
                bb.seek(offset)
                self.contents.append(bb.read(size))
                
                bb.seek(current)
                
    def save(self, bb):
        """Save modified BB contents to a new file"""        
        bb.write('BB\x00\x00')
        # Write len of files
        bb.write(pack('<i', len(self.contents)))
        
        # Write files info and data
        offset = len(self.contents) * 8 + 8
        
        for bbg in self.contents:
            # Write info
            bb.write(pack('<ii', offset, len(bbg)))
            next_entry = bb.tell()
            
            bb.seek(offset)
            bb.write(bbg)
            offset = bb.tell()
            
            bb.seek(next_entry)
        
        
def read_compressed(bbg, offset):
    # Decompress data
    bbg.seek(offset)
    
    header = unpack("<I", bbg.read(4))[0]    
    compression_type, uncompressed_length = header & 0xFF, header >> 8
            
    if compression_type == 0:
        # Uncompressed
        return bbg.read(uncompressed_length)
    elif compression_type == 0x10:
        return WiiLZ77(bbg, offset).uncompress()
    else:
        raise ValueError
        
def temp_palette():
    palette = []
    # Alpha
    palette += [255, 0, 255]
    # Empty
    palette += [0, 0, 255] * 13
    # White
    palette += [0, 0, 255] #[255, 255, 255]
    palette += [0, 0, 255] #[0, 0, 0]
    palette += [0, 0, 255] * 224   
    # poly
    palette += [0, 0, 0]
    palette += [0, 0, 255] * 14
    #palette += [222, 222, 222]
    #palette += [238, 238, 238]
    palette += [255, 255, 255]
    
    return palette
        
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

    # copy metadata into new object
    for k,v in im.info.iteritems():
        if k in reserved: continue
        meta.add_text(k, v, 0)
        
    # and save
    im.save(file, "PNG", pnginfo=meta)
        
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Rip bb/bbg from Sonic Rush to png(s) and import back')
    parser.add_argument('files', help='files to work with', type=argparse.FileType('r+b'), nargs="+")
    parser.add_argument('-b', '--bbg', help='only extract bbg images from bb archive', action='store_true')
    parser.add_argument('-p', '--palette', help="external bbg palette", type=argparse.FileType('rb'))
    parser.add_argument('-u', '--update', help='update bb file from images', type=argparse.FileType('rb'), nargs="+")

    args = parser.parse_args()
    
    for input_file in args.files:
        if input_file.name.endswith('.bbg'):
            # Single image file
            bbg = BBG(input_file)
            
            try:
                image = bbg.to_image(args.palette)
            except Exception as e:
                print '{} while loading {}'.format(e, input_file.name)
                raise
                continue
            
            if args.update:
                image = Image.open(args.update[0])
                bbg.update(image)
                continue
            
            bbg_name = os.path.splitext(os.path.basename(input_file.name))[0]
            
            if args.palette:
                bbg_palette_name = os.path.splitext(os.path.basename(args.palette.name))[0]
                image_name = '{}_{}.png'.format(bbg_name, bbg_palette_name)
            else:
                image_name = bbg_name + '.png'

            # Use wrapper for preserving metadata
            with open(image_name, 'wb') as output:
                pngsave(image, output)
                
        elif input_file.name.endswith('.bb'):
            # bb archive
            bb = BB(input_file)
            name = os.path.splitext(os.path.basename(input_file.name))[0]
            
            if args.update:            
                update = {}
                name = os.path.splitext(os.path.basename(input_file.name))[0]
                
                for image in args.update:
                    image_name, i = os.path.splitext(os.path.basename(image.name))[0].rsplit('_', 1)
                    
                    if image_name == name:
                        update[int(i)] = Image.open(image)
                    else:
                        raise ValueError("Wrong image filename: " + image.name)
            
            for n, bbg_data in enumerate(bb.contents):                
                if args.bbg:
                    # Just store bbgs for debug
                    bbg_name = '{}_{:03}.bbg'.format(name, n + 1)
                    with open(bbg_name, 'wb') as output:
                        output.write(bbg_data)
                else:                    
                    # Load image
                    bbg = BBG(StringIO(bbg_data))
                    
                    if args.update:
                        if n + 1 in update:
                            # We need to update bbg with new images
                            bbg.update(update[n + 1])
                            bb.contents[n] = bbg.bbg.getvalue()                        
                    else:
                        image = bbg.to_image()
                        image_name = '{}_{:03}.png'.format(name, n + 1)
                        image.save(image_name)
            
            if args.update:                
                bb.save(open('{}_updated.bb'.format(name), 'w'))