import os, math, struct
from mathutils import Euler, Matrix
from . import gtx
from .classes import *
from .const import *
from ..file_io import *

encodings = {
        #0x30: ???
        0x40: 'I4',
        0x41: 'IA4',
        0x42: 'I8',
        0x43: 'IA8',
        0x44: 'RGB565',
        0x45: 'RGBA32',
        0x90: 'RGB5A3',
        0xB0: 'CMPR',
    }

mesh_dict = {}
mat_dict = {}
tex_dict = {}
img_dict = {}

def readString(file, address):
    s = ''
    nextChar = file.read('uchar', address)
    while nextChar != 0:
        s += chr(nextChar)
        nextChar = file.read('uchar', 0, whence='current')
    return s

def toRotationMatrix(x, y, z):
    return Euler((x, y, z), 'XYZ').to_matrix().to_4x4()

def toScaleMatrix(x, y, z):
    sX = Matrix.Scale(x, 4, (1, 0, 0))
    sY = Matrix.Scale(y, 4, (0, 1, 0))
    sZ = Matrix.Scale(z, 4, (0, 0, 1))
    return sZ @ sY @ sX

def flattenIndexedDict(d):
    return [data['object'] for addr,data in
            sorted(d.items(), key=lambda item: item[1]['index'])]

def parseTextures(file, address, numTextures):
    for i in range(numTextures):
        textureAddr = file.read('uint', address, offset=(4 * i))
        # technically this is the horizontal extrapolation but
        # just gonna use it for both right now
        extrap = file.read('uint', textureAddr, offset=0x10)
        imageOffset = file.read('uint', textureAddr, offset=0x28)
        imageAddr = textureAddr + imageOffset
        if imageAddr not in img_dict:
            img = decompressImage(file, textureAddr, imageAddr)
            img_dict[imageAddr] = {
                'object': img,
                'index': len(img_dict)
            }
        tex = Texture(img_dict[imageAddr]['index'], extrap)
        tex_dict[textureAddr] = {
            'object': tex,
            'index': len(tex_dict)
        }
        
def decompressImage(file, texAddress, imageAddr):
    width = file.read('ushort', texAddress, offset=0)
    height = file.read('ushort', texAddress, offset=0x2)
    encoding = file.read('uint', texAddress, offset=0x8)
    size = file.read('uint', texAddress, offset=0x4c)
    compressedData = file.read_chunk(imageAddr, size)
    imageData = gtx.decompress(compressedData,
                               width, height,
                               encodings[encoding])
    image = Image(imageData, width, height)
    return image

def parseMaterials(file, address, numMaterials):
    for i in range(numMaterials):
        materialAddr = file.read('uint', address, offset=(4 * i))
        mat_dict[materialAddr] = {
            'object': parseMaterial(file, materialAddr),
            'index': len(mat_dict)
        }

def parseMaterial(file, address):
    nameAddr = file.read('uint', address, offset=0)
    name = file.read('string', nameAddr)
    textureAddr = file.read('uint', address, offset=0x18)
    mat = Material(name,
                   tex_dict[textureAddr]['index'] if textureAddr else None)
    return mat

def parseVertices(file, address, numEntries, stride):
    vertices = []
    for i in range(numEntries):
        x = file.read('float', address, offset=(i * stride))
        y = file.read('float', 0, whence='current')
        z = file.read('float', 0, whence='current')
        vertices.append((x, y, z))
    return vertices

def parseNormals(file, address, numEntries, stride):
    normals = []
    for i in range(numEntries):
        nx = file.read('float', address, offset=(i * stride + 0xc))
        ny = file.read('float', 0, whence='current')
        nz = file.read('float', 0, whence='current')
        normals.append((nx, ny, nz))
    return normals

def parseTextureCoords(file, address, numEntries, stride):
    texcoords = []
    for i in range(numEntries):
        x = file.read('float', address, offset=(i * stride))
        # mirror vertically
        y = file.read('float', 0, whence='current')
        texcoords.append((x, y))
    return texcoords

def parseWeights(file, address):
    weights = []
    
    n = file.read('ushort', address, offset=0)
    addr1 = file.read('uint', address, offset=0x4)
    file.seek(addr1)
    for i in range(n):
        numVerts = file.read('ushort', 0, whence='current')
        bone1 = file.read('ushort', 0, whence='current')
        for j in range(numVerts):
            weights.append({bone1: 1.0})

    n = file.read('ushort', address, offset=0x8)
    addr1 = file.read('uint', address, offset=0xc)
    addr2 = file.read('uint', address, offset=0x10)
    count = 0
    for i in range(n):
        numVerts = file.read('ushort', addr1, offset=(6 * i))
        bone1 = file.read('ushort', 0, whence='current')
        bone2 = file.read('ushort', 0, whence='current')
        file.seek(addr2 + 2 * count)
        for j in range(numVerts):
            # weights need to be normalized
            w = file.read('ushort', 0, whence='current') / 0xffff
            weights.append({bone1: w, bone2: 1 - w})
        count += numVerts

    n = file.read('ushort', address, offset=0x14)
    addr1 = file.read('uint', address, offset=0x18)
    file.seek(addr1)
    for i in range(n):
        vertNum = file.read('ushort', 0, whence='current')
        bone1 = file.read('ushort', 0, whence='current')
        bone2 = file.read('ushort', 0, whence='current')
        # weights need to be normalized
        w1 = file.read('ushort', 0, whence='current') / 0xffff
        w2 = file.read('ushort', 0, whence='current') / 0xffff
        for bone in weights[vertNum]:
            weights[vertNum][bone] *= (1 - w1 - w2)
        weights[vertNum][bone1] = w1
        if bone2 != 0xffff:
            weights[vertNum][bone2] = w2

    return weights

def parseFaces(file, address, numGroups, vertAttrs):
    faces = []
    file.seek(address)
    for i in range(numGroups):
        op = file.read('uchar', 0, whence='current')
        count = file.read('ushort', 0, whence='current')
        vertices = []
        for j in range(count):
            v = n = t = None
            for i in range(len(vertAttrs)):
                if vertAttrs[i] == GX_VA_POS:
                    v = file.read('ushort', 0, whence='current')
                elif vertAttrs[i] in [GX_VA_NRM, GX_VA_NBT]:
                    n = file.read('ushort', 0, whence='current')
                elif vertAttrs[i] == GX_VA_TEX0:
                    t = file.read('ushort', 0, whence='current')
                else:
                    file.read(2)
            vertices.append((v, n, t))
        
        if op == GX_DRAW_QUADS:
            for i in range(0, count, 4):
                faces.append(
                    Face(*zip(vertices[i+1], vertices[i], vertices[i+2])))
                faces.append(
                    Face(*zip(vertices[i+2], vertices[i], vertices[i+3])))
        elif op == GX_DRAW_TRIANGLES:
            for i in range(0, count, 3):
                faces.append(
                    Face(*zip(vertices[i+1], vertices[i], vertices[i+2])))
        elif op == GX_DRAW_TRIANGLE_STRIP:
            for i in range(count - 2):
                if i % 2 == 0:
                    faces.append(
                        Face(*zip(vertices[i+1], vertices[i], vertices[i+2])))
                else:
                    faces.append(
                        Face(*zip(vertices[i], vertices[i+1], vertices[i+2])))
        else:
            raise Exception(f"Unknown opcode '{k}' at offset {hex(file.tell())}")
    return faces

def parseMesh(file, address):
    meshHeaderAddr = file.read('uint', address, offset=0x18)
    vertInfoAddr = file.read('uint', meshHeaderAddr, offset=0x10)
    attr = file.read('uchar', vertInfoAddr)
    while attr != 0xff:
        stride = file.read('uchar', 0x4, whence='current')
        if attr == GX_VA_POS:
            vertStride = stride
        elif attr == GX_VA_TEX0:
            texStride = stride
        attr = file.read('uchar', 0x2, whence='current')
    
    # vertices
    numVertices = file.read('ushort', address, offset=0x2)
    verticesAddr = file.read('uint', address, offset=0x8)
    v = parseVertices(file, verticesAddr, numVertices, vertStride)
    # vertex normals
    n = parseNormals(file, verticesAddr, numVertices, vertStride)
    # texture coordinates
    uvLayerAddr = file.read('uint', address, offset=0x14)
    t = None
    if uvLayerAddr != 0:
        texCoordsAddr = file.read('uint', uvLayerAddr, offset=0)
        numTexCoords = file.read('ushort', uvLayerAddr, offset=0x2)
        t = parseTextureCoords(file, texCoordsAddr, numTexCoords, texStride)

    # bone weights
    boneWeightsAddr = file.read('uint', address, offset=0xc)
    if boneWeightsAddr != 0:
        w = parseWeights(file, boneWeightsAddr)
    else:
        w = None

    meshGroup = Mesh(v, n, t, w)
    for mesh in parseMeshPart(file, meshHeaderAddr):
        meshGroup.parts.append(mesh)
    return meshGroup

def parseMeshPart(file, address):
    vertInfoAddr = file.read('uint', address, offset=0x10)
    vas = []
    va = file.read('uchar', vertInfoAddr)
    while va != 0xff:
        vas.append(va)
        va = file.read('uchar', 0x7, whence='current')
    
    materialAddr = file.read('uint', address, offset=0x8)
    numGroups = file.read('ushort', address, offset=0xc)
    facesAddr = file.read('uint', address, offset=0x14)
    f = parseFaces(file, facesAddr, numGroups, vas)
    mesh = MeshPart(f, GX_VA_TEX0 in vas, mat_dict[materialAddr]['index'])
    yield mesh
    
    # check if there is a next part of the mesh
    nextMeshAddr = file.read('uint', address, offset=0x1c)
    if nextMeshAddr != 0:
        for mesh in parseMeshPart(file, nextMeshAddr):
            yield mesh

def parseSkeleton(file, address):
    objNameAddr = file.read('uint', address, offset=0)
    name = file.read('string', objNameAddr)
    numBones = file.read('ushort', address, offset=0x6)
    rootAddr = file.read('uint', address, offset=0x10)
    bones = [None] * numBones
    rootBone = next(parseBones(file, rootAddr, bones))
    return Skeleton(name, numBones, bones)

def parseBones(file, address, bones):
    k = file.read('uint', address, offset=0)
    nameAddr = file.read('uint', address, offset=0x4)
    name = file.read('string', nameAddr)
    idx = file.read('ushort', address, offset=0x8)
    
    posAddr = file.read('uint', address, offset=0xc)
    if posAddr != 0:
        x = file.read('float', posAddr)
        y = file.read('float', 0, whence='current')
        z = file.read('float', 0, whence='current')
        pos = Matrix.Translation((x, y, z))
    else:
        pos = Matrix.Identity(4)
        
    rotAddr = file.read('uint', address, offset=0x10)
    if rotAddr != 0:
        rx = file.read('float', rotAddr)
        ry = file.read('float', 0, whence='current')
        rz = file.read('float', 0, whence='current')
        rot = toRotationMatrix(rx, ry, rz)
    else:
        rot = Matrix.Identity(4)
        
    scaAddr = file.read('uint', address, offset=0x14)
    if scaAddr != 0:
        file.seek(scaAddr)
        sx = file.read('float', scaAddr)
        sy = file.read('float', 0, whence='current')
        sz = file.read('float', 0, whence='current')
        sca = toScaleMatrix(sx, sy, sz)
    else:
        sca = Matrix.Identity(4)
        
    if k == 0x2:
        # secondary rotation
        rx = file.read('float', address, offset=0x34)
        ry = file.read('float', 0, whence='current')
        rz = file.read('float', 0, whence='current')
        rot2 = toRotationMatrix(rx, ry, rz)
        rot = rot2 @ rot
        # inverse bind matrix
        mat = []
        file.seek(address + 0x44)
        for r in range(3):
            row = []
            for c in range(4):
                row.append(file.read('float', 0, whence='current'))
            mat.append(row)
        mat.append([0.0, 0.0, 0.0, 1.0])
    else:
        mat = [[1.0, 0.0, 0.0, 0.0],
               [0.0, 1.0, 0.0, 0.0],
               [0.0, 0.0, 1.0, 0.0],
               [0.0, 0.0, 0.0, 1.0]]
    mat = Matrix(mat)
        
    bone = Bone(idx, name, (pos @ rot @ sca), mat)
    bones[idx] = bone
    
    childAddr = file.read('uint', address, offset=0x24)
    if childAddr != 0:
        for child in parseBones(file, childAddr, bones):
            bone.childIndices.append(child.index)
            child.parentIndex = idx
            
    if k == 0x3: # skin node
        meshAddr = file.read('uint', address, offset=0x30)
        if meshAddr not in mesh_dict:
            mesh_dict[meshAddr] = {
                'object': parseMesh(file, meshAddr),
                'index': len(mesh_dict)
            }
        bone.meshIndex = mesh_dict[meshAddr]['index']
    yield bone
    
    nextAddr = file.read('uint', address, offset=0x28)
    if nextAddr != 0:
        for sibling in parseBones(file, nextAddr, bones):
            yield sibling

def parseSDR(path):
    global mesh_dict, mat_dict, tex_dict, img_dict
    mesh_dict = {}
    mat_dict = {}
    tex_dict = {}
    img_dict = {}

    file = BinaryReader(path)

    # textures
    texturesListAddr = file.read('uint', 0xc)
    numTextures = file.read('ushort', 0x1a)
    parseTextures(file, texturesListAddr, numTextures)
    
    # materials
    materialsListAddr = file.read('uint', 0x14)
    numMaterials = file.read('ushort', 0x1e)
    parseMaterials(file, materialsListAddr, numMaterials)
    
    # skeleton
    skeletonsListAddrPtr = file.read('uint', 0x8)
    numSkeletons = file.read('ushort', 0x18)
    skeletons = []
    for i in range(numSkeletons):
        skeletonHeaderAddr = file.read('uint', skeletonsListAddrPtr + 4 * i)
        skeletons.append(parseSkeleton(file, skeletonHeaderAddr))
    
    file.close()
    
    sdr = {
        'skeletons': skeletons,
        'meshes': flattenIndexedDict(mesh_dict),
        'materials': flattenIndexedDict(mat_dict),
        'textures': flattenIndexedDict(tex_dict),
        'images': flattenIndexedDict(img_dict)
    }
    return sdr
