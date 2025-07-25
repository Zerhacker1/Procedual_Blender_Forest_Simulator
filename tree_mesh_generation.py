# ##### BEGIN GPL LICENSE BLOCK #####
#
#  SCA Tree Generator, a Blender addon
#  (c) 2013, 2014 Michel J. Anders (varkenvarken)
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

# for first time
import sys

from .endpoint_sampling import sample_mesh_group_surface_points
sys.path.append("C:\\users\\anton\\appdata\\roaming\\python\\python39\\site-packages")

bl_info = {
  "name": "SCA Tree Generator",
  "author": "michel anders (varkenvarken)",
  "version": (0, 2, 14),
  "blender": (2, 93, 0),
  "location": "View3D > Add > Mesh",
  "description": "Adds a tree created with the space colonization algorithm starting at the 3D cursor",
  "warning": "",
  "wiki_url": "https://github.com/varkenvarken/spacetree/wiki",
  "tracker_url": "",
  "category": "Add Mesh"}

from time import time
import random
from functools import partial
from math import radians, sin,cos
import numpy as np

import bpy
from bpy.props import FloatProperty, IntProperty, BoolProperty, EnumProperty
from mathutils import Vector,Euler,Matrix,Quaternion
from scipy.spatial import KDTree
import bmesh

from .sca import SCA, Branchpoint # the core class that implements the space colonization algorithm and the definition of a segment
from .timer import Timer
from .utils import load_materials_from_bundled_lib, load_particlesettings_from_bundled_lib, get_vertex_group
from .voxel_grid import VoxelGrid

def availableGroups(self, context):
  return [(name, name, name, n) for n,name in enumerate(bpy.data.collections.keys())]

def availableGroupsOrNone(self, context):
  groups = [ ('None', 'None', 'None', 0) ]
  return groups + [(name, name, name, n+1) for n,name in enumerate(bpy.data.collections.keys())]

def availableObjects(self, context):
  return [(name, name, name, n+1) for n,name in enumerate(bpy.data.objects.keys())]

barkmaterials = None

def availableParticleSettings(self, context, particlesettings):
  # im am not sure why self.__class__.particlesettings != bpy.types.MESH_OT_sca_tree ....
  settings = [ ('None', 'None', 'None', 0) ]
  #    return settings + [(name, name, name, n+1) for n,name in enumerate(bpy.types.MESH_OT_sca_tree.particlesettings.keys())]
  # (identifier, name, description, number)
  # note, when we create a new tree the particles settings will be made unique so they can be tweaked individually for
  # each tree. That also means they will  have distinct names, but we manipulate those to be displayed in a consistent way
  return settings + [(name, name.split('.')[0], name, n+1) for n,name in enumerate(particlesettings.keys())]

def availableBarkMaterials(self, context):
  global barkmaterials
  return [(name, name.split('.')[0], name, n) for n,name in enumerate(barkmaterials.keys())]

def ellipsoid(r=5,rz=5,p=Vector((0,0,8)),taper=0):
  r2=r*r
  z2=rz*rz
  if rz>r : r = rz
  while True:
    x = (random.random()*2-1)*r
    y = (random.random()*2-1)*r
    z = (random.random()*2-1)*r
    f = (z+r)/(2*r)
    f = 1 + f*taper if taper>=0 else (1-f)*-taper
    if f*x*x/r2+f*y*y/r2+z*z/z2 <= 1:
      yield p+Vector((x,y,z))

def pointInsideMesh(pointrelativetocursor,ob):
  # adapted from http://blenderartists.org/forum/showthread.php?195605-Detecting-if-a-point-is-inside-a-mesh-2-5-API&p=1691633&viewfull=1#post1691633
  mat = ob.matrix_world.inverted()
  orig = mat@(pointrelativetocursor+bpy.context.scene.cursor.location)
  count = 0
  axis=Vector((0,0,1))
  while True:
    _, location,normal,index = ob.ray_cast(orig,orig+axis*10000.0)
    if index == -1: break
    count += 1
    orig = location + axis*0.00001
  if count%2 == 0:
    return False
  return True
  
def ellipsoid2(rxy=5,rz=5,p=Vector((0,0,8)),surfacebias=1,topbias=1):
  while True:
    phi = 6.283*random.random()
    theta = 3.1415*(random.random()-0.5)
    r = random.random()**((1.0/surfacebias)/2)
    x = r*rxy*cos(theta)*cos(phi)
    y = r*rxy*cos(theta)*sin(phi)
    st=sin(theta)
    st = (((st+1)/2)**(1.0/topbias))*2-1
    z = r*rz*st
    #print(">>>%.2f %.2f %.2f "%(x,y,z))
    m = p+Vector((x,y,z))
# undocumented feature: bombs if any selected object is not a mesh. Use crown and shadow/exclusion groups instead
#        reject = False
#        for ob in bpy.context.selected_objects:
#            # probably we should check if each object is a mesh
#            if pointInsideMesh(m,ob) :
#                reject = True
#                break
#        if not reject:
    yield m

def halton3D(index):
  """
  return a quasi random 3D vector R3 in [0,1].
  each component is based on a halton sequence. 
  quasi random is good enough for our purposes and is 
  more evenly distributed then pseudo random sequences. 
  See en.m.wikipedia.org/wiki/Halton_sequence
  """

  def halton(index, base):
    result=0
    f=1.0/base
    I=index
    while I>0:
      result += f*(I%base)
      I=int(I/base)
      f/=base
    return result
  return Vector((halton(index,2),halton(index,3),halton(index,5)))

def insidegroup(pointrelativetocursor, group):
  if group not in bpy.data.collections : return False
  for ob in bpy.data.collections.get(group).objects:
    if isinstance(ob.data, bpy.types.Mesh) and pointInsideMesh(pointrelativetocursor,ob):
      return True
  return False

def groupdistribution(crowngroup,shadowgroup=None,shadowdensity=0.5, seed=0,size=Vector((1,1,1)),pointrelativetocursor=Vector((0,0,0))):
  if crowngroup == shadowgroup:
    shadowgroup = None # safeguard otherwise every marker would be rejected
  nocrowngroup = crowngroup not in bpy.data.collections
  noshadowgroup = (shadowgroup is None) or (shadowgroup not in bpy.data.collections) or (shadowgroup == 'None')
  index=100+seed
  nmarkers=0
  nyield=0
  while True:
    nmarkers+=1
    v = halton3D(index)
    v[0] *= size[0]
    v[1] *= size[1]
    v[2] *= size[2]
    v+=pointrelativetocursor
    index+=1
    insidecrown = nocrowngroup or insidegroup(v,crowngroup)
    outsideshadow = noshadowgroup # if there's no shadowgroup we're always outside of it
    if not outsideshadow:
      inshadow = insidegroup(v,shadowgroup) # if there is, check if we're inside the group
      if not inshadow:
        outsideshadow = True
      else:
        outsideshadow = random.random() > shadowdensity  # if inside the group we might still generate a marker if the density is low
    # if shadowgroup overlaps all or a significant part of the crowngroup
    # no markers will be yielded and we would be in an endless loop.
    # so if we yield too few correct markers we start yielding them anyway.
    lowyieldrate = (nmarkers>200) and (nyield/nmarkers < 0.01)
    if (insidecrown and outsideshadow) or lowyieldrate:
      nyield+=1
      yield v

def surface_based_groupdistribution(crowngroup, n_points=1000, seed=0, size=Vector((1,1,1)), pointrelativetocursor=Vector((0,0,0))):
    """Generate points on mesh surfaces instead of checking if points are inside"""
    
    # Pre-generate surface points for crown group
    crown_surface_points = []
    if crowngroup in bpy.data.collections:
        crown_surface_points = sample_mesh_group_surface_points(crowngroup, n_points, seed)
    
    return crown_surface_points
    
def groupExtends(group):
  """
  return a size,minimum tuple both Vector elements, describing the size and position
  of the bounding box in world space that encapsulates all objects in a group.
  """
  bb=[]
  if group in bpy.data.collections:
    for ob in bpy.data.collections[group].objects:
      rot = ob.matrix_world.to_quaternion()
      scale = ob.matrix_world.to_scale()
      translate = ob.matrix_world.translation
      for v in ob.bound_box: # v is not a vector but an array of floats
        p = ob.matrix_world @ Vector(v[0:3])
        bb.extend(p[0:3])
    mx = Vector((max(bb[0::3]), max(bb[1::3]), max(bb[2::3])))
    mn = Vector((min(bb[0::3]), min(bb[1::3]), min(bb[2::3])))
    return mx-mn,mn
  return Vector((2,2,2)),Vector((-1,-1,-1)) # a 2x2x2 cube when the group does not exist
  
def createMarkers(tree,scale=0.05):
  #not used as markers are parented to tree object that is created at the cursor position
  #p=bpy.context.scene.cursor.location
  
  verts=[]
  faces=[]

  tetraeder = [Vector((-1,1,-1)),Vector((1,-1,-1)),Vector((1,1,1)),Vector((-1,-1,1))]
  tetraeder = [v * scale for v in tetraeder]
  tfaces = [(0,1,2),(0,1,3),(1,2,3),(0,3,2)]
  
  for eip,ep in enumerate(tree.endpoints):
    verts.extend([ep + v for v in tetraeder])
    n=len(faces)
    faces.extend([(f1+n,f2+n,f3+n) for f1,f2,f3 in tfaces])
    
  mesh = bpy.data.meshes.new('Markers')
  mesh.from_pydata(verts,[],faces)
  mesh.update(calc_edges=True)
  return mesh

def basictri(bp, verts, radii, power, scale, p):
  v = bp.v + p
  nv = len(verts)
  r=(bp.connections**power)*scale
  a=-r
  b=r*0.5   # cos(60)
  c=r*0.866 # sin(60)
  verts.extend([v+Vector((a,0,0)), v+Vector((b,-c,0)), v+Vector((b,c,0))]) # provisional, should become an optimally rotated triangle
  radii.extend([bp.connections,bp.connections,bp.connections])
  return (nv, nv+1, nv+2)
  
def _simpleskin(bp, loop, verts, faces, radii, power, scale, p):
  newloop = basictri(bp, verts, radii, power, scale, p)
  for i in range(3):
    faces.append((loop[i],loop[(i+1)%3],newloop[(i+1)%3],newloop[i]))
  if bp.apex:
    _simpleskin(bp.apex, newloop, verts, faces, radii, power, scale, p)
  if bp.shoot:
    _simpleskin(bp.shoot, newloop, verts, faces, radii, power, scale, p)
  
def simpleskin(bp, verts, faces, radii, power, scale, p):
  loop = basictri(bp, verts, radii, power, scale, p)
  if bp.apex:
    _simpleskin(bp.apex, loop, verts, faces, radii, power, scale, p)
  if bp.shoot:
    _simpleskin(bp.shoot, loop, verts, faces, radii, power, scale, p)

#TODO: Make it better than just random
def leafnode(bp, verts, faces, radii, p1, p2, scale=0.0001):
  loop1 = basictri(bp, verts, radii, 0.0, scale, p1)
  loop2 = basictri(bp, verts, radii, 0.0, scale, p2)
  # if random() > random_threshold:
  #   for i in range(3):
  #     faces.append((loop1[i],loop1[(i+1)%3],loop2[(i+1)%3],loop2[i]))
  for i in range(3):
    faces.append((loop1[i],loop1[(i+1)%3],loop2[(i+1)%3],loop2[i]))
  if bp.apex:
    leafnode(bp.apex, verts, faces, radii, p1, p2, scale)
  if bp.shoot:
    leafnode(bp.shoot, verts, faces, radii, p1, p2, scale)

def createLeaves2(tree, roots, p, scale):
  verts = []
  faces = []
  radii = []
  for r in roots:
    leafnode(r, verts, faces, radii, p, p++Vector((0,0, scale)), scale)
  mesh = bpy.data.meshes.new('LeafEmitter')
  mesh.from_pydata(verts, [], faces)
  mesh.update(calc_edges=True)
  return mesh, verts, faces, radii

def pruneTree(tree, generation):
  nbp = []
  i2p = {}
  #print()
  for i,bp in enumerate(tree):
    #print(i, bp.v, bp.generation, bp.parent, end='')
    if bp.generation >= generation:
      #print(' keep', end='')
      bp.index = i
      i2p[i] = len(nbp)
      nbp.append(bp)
    #print()
  return nbp, i2p
  
def createGeometry(tree, power=0.5, scale=0.01,
  nomodifiers=True, skinmethod='NATIVE', subsurface=False,
  bleaf=4.0,
  leafParticles='None',
  leaf_density=[1.0, 1.0],
  particlesettings=None,
  objectParticles='None',
  emitterscale=0.1,
  timeperf=True,
  addLeaves=False,
  prune=0,
  class_id=0):
  
  if particlesettings is None and leafParticles != 'None':
    raise ValueError("No particlesettings available, cannot create leaf particles")
  
  timings = Timer()
  
  p=bpy.context.scene.cursor.location
  verts=[]
  edges=[]
  faces=[]
  radii=[]
  roots=set()
  
  # prune if requested
  tree.branchpoints, index2position = pruneTree(tree.branchpoints, prune)
  if len(tree.branchpoints) < 2:
    return None
  # Loop over all branchpoints and create connected edges
  #print('\ngenerating skeleton')
  
  for n,bp in enumerate(tree.branchpoints):
    #print(n, bp.index, bp.v, bp.generation, bp.parent)
    verts.append(bp.v+p)
    radii.append(bp.connections)
    if not (bp.parent is None) :
      #print(bp.parent,index2position[bp.parent])
      edges.append((len(verts)-1,index2position[bp.parent]))
    else :
      nv=len(verts)
      roots.add(bp)
    bp.index=n
    
  timings.add('skeleton')
  
  # native skinning method
  if nomodifiers == False and skinmethod == 'NATIVE': 
    # add a quad edge loop to all roots
    for r in roots:
      simpleskin(r, verts, faces, radii, power, scale, p)
      
  # end of native skinning section
  timings.add('nativeskin')
  
  # create the (skinned) tree mesh
  mesh = bpy.data.meshes.new('Tree')
  mesh.from_pydata(verts, edges, faces)
  mesh.update(calc_edges=True)
  
  # create the tree object an make it the only selected and active object in the scene
  obj_new = bpy.data.objects.new(mesh.name, mesh)
  bpy.context.view_layer.active_layer_collection.collection.objects.link(obj_new)
  # bpy.context.collection.objects.link(obj_new)
  for ob in bpy.context.scene.objects:
    ob.select_set(False)
  bpy.context.view_layer.objects.active = obj_new
  obj_new.select_set(True)
  # bpy.context.scene.objects.active = obj_new
  bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
  
  # add a leaves vertex group
  # leavesgroup = get_vertex_group(bpy.context, 'Leaves')
  
  # maxr = max(radii) if len(radii)>0 else 0.03 # pruning might have been so aggressive that there are no radii (NB. python 3.3 does not know the default keyword for the max() fie
  # if maxr<=0 : maxr=1.0
  # maxr=float(maxr)
  # for v,r in zip(mesh.vertices,radii):
  #   leavesgroup.add([v.index], (1.0-r/maxr)**bleaf, 'REPLACE')
  timings.add('createmesh')
  
  # add a subsurf modifier to smooth the branches 
  if nomodifiers == False:
    if subsurface:
      bpy.ops.object.modifier_add(type='SUBSURF')
      bpy.context.active_object.modifiers[0].levels = 1
      bpy.context.active_object.modifiers[0].render_levels = 1
      bpy.context.active_object.modifiers[0].uv_smooth = 'PRESERVE_CORNERS'

    # add a skin modifier
    if skinmethod == 'BLENDER':
      
      bpy.ops.object.modifier_add(type='SKIN')
      bpy.context.active_object.modifiers[-1].use_smooth_shade=True
      bpy.context.active_object.modifiers[-1].use_x_symmetry=True
      bpy.context.active_object.modifiers[-1].use_y_symmetry=True
      bpy.context.active_object.modifiers[-1].use_z_symmetry=True
      
      skinverts = bpy.context.active_object.data.skin_vertices[0].data

      for i,v in enumerate(skinverts):
        v.radius = [(radii[i]**power)*scale,(radii[i]**power)*scale]
        if i in roots:
          v.use_root = True
      
      # add an extra subsurf modifier to smooth the skin
      bpy.ops.object.modifier_add(type='SUBSURF')
      bpy.context.active_object.modifiers[-1].levels = 1
      bpy.context.active_object.modifiers[-1].render_levels = 2
      #Changed from me
      #bpy.context.active_object.modifiers[-1].use_subsurf_uv = True
      # to (same above)
      # bpy.context.active_object.modifiers[0].uv_smooth = 'PRESERVE_CORNERS'
      
  timings.add('modifiers')
  # create a particles based leaf emitter (if we have leaves and/or objects)
  # bpy.context.scene.objects.active = obj_new
  obj_processed = segmentIntoTrunkAndBranch(tree, obj_new, (np.array(radii)**power)*scale)
  bpy.ops.object.shade_smooth()

  obj_processed["class_id"] = class_id
  obj_processed.name = f"Tree_{obj_processed['class_id']}"
  
  if leafParticles != 'None' or objectParticles != 'None':
    mesh, verts, faces, radii = createLeaves2(tree, roots, Vector((0,0,0)), emitterscale)
    obj_leaves2 = bpy.data.objects.new(mesh.name, mesh)
    base = bpy.context.collection.objects.link(obj_leaves2)
    obj_leaves2.parent = obj_processed
    # bpy.context.scene.objects.active = obj_leaves2
    bpy.context.view_layer.objects.active = obj_leaves2
    obj_leaves2.select_set(True)
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
    # add a LeafDensity vertex group to the LeafEmitter object
    leavesgroup = get_vertex_group(bpy.context, 'LeafDensity')
    maxr = max(radii)
    if maxr<=0 : maxr=1.0
    maxr=float(maxr)
    for v,r in zip(mesh.vertices,radii):
      leavesgroup.add([v.index], (1.0-r/maxr)**bleaf, 'REPLACE')

    if leafParticles != 'None':
      bpy.ops.object.particle_system_add()
      obj_leaves2["class_id"] = class_id
      obj_leaves2.name = f"Leaves_{obj_leaves2['class_id']}"
      obj_leaves2.particle_systems.active.settings = particlesettings[leafParticles]
      # obj_leaves2.particle_systems.active.settings.count = len(faces)
      obj_leaves2.particle_systems.active.settings.count = int(len(faces) * random.uniform(leaf_density[0], leaf_density[1]))
      obj_leaves2.particle_systems.active.name = 'Leaves'
      obj_leaves2.particle_systems.active.vertex_group_density = leavesgroup.name
      
      bpy.context.view_layer.objects.active = obj_leaves2
      obj_leaves2.select_set(True)
      bpy.ops.object.duplicates_make_real()
      
      for leaf_idx, obj in enumerate(bpy.context.selected_objects):
        if obj != obj_leaves2 and obj != obj_processed:
          
          obj["class_id"] = class_id
          obj.name = f"Leaf_{obj['class_id']}_{leaf_idx}"
          
          world_matrix = obj.matrix_world.copy()
          
          obj.parent = obj_processed
          
          obj.matrix_world = world_matrix
      
      bpy.context.view_layer.objects.active = obj_leaves2
      bpy.ops.object.particle_system_remove()
      
    if objectParticles != 'None':
      bpy.ops.object.particle_system_add()
      obj_leaves2.particle_systems.active.settings = particlesettings[objectParticles]
      obj_leaves2.particle_systems.active.settings.count = len(faces)
      obj_leaves2.particle_systems.active.name = 'Objects'
      obj_leaves2.particle_systems.active.vertex_group_density = leavesgroup.name
      
      bpy.context.view_layer.objects.active = obj_leaves2
      obj_leaves2.select_set(True)
      bpy.ops.object.duplicates_make_real()
      
      for obj_idx, obj in enumerate(bpy.context.selected_objects):
        if obj != obj_leaves2 and obj != obj_processed:
          
          obj["class_id"] = class_id
          obj.name = f"Object_{obj['class_id']}_{obj_idx}"
          
          world_matrix = obj.matrix_world.copy()
          
          obj.parent = obj_processed
          
          obj.matrix_world = world_matrix
      
      bpy.context.view_layer.objects.active = obj_leaves2
      bpy.ops.object.particle_system_remove()
    
    bpy.data.objects.remove(obj_leaves2, do_unlink=True)
  
  timings.add('leaves')
  
  if timeperf:
    print(timings)
    
  # bpy.data.objects.remove(obj_new, do_unlink=True)
  return obj_processed

# This method is currently not being used.
def add_leaves_to_tree(tree, leave_nodes, obj_new):
  # Create a new mesh for the leaves
  leaf_mesh = bpy.data.meshes.new("Leaves")
  leaf_verts = []
  leaf_faces = []
  
  # uv_layer = leaf_mesh.loops.layers.uv.new()
  
  for leave_node in leave_nodes:
    pos = leave_node.v
    direction = (pos - tree.branchpoints[leave_node.parent].v).normalized() if leave_node.parent is not None else Vector((0, 0, 1))

    # First quad
    v1 = Vector((-0.1,-0.1,0))
    v2 = Vector((0.1,-0.1,0))
    v3 = Vector((0.1,0.1,0))
    v4 = Vector((-0.1,0.1,0))

    # Rotate the second quad vertices 90° from the direction vector
    # current_direction = Vector((0, 0, 1))  # Assuming the initial direction is along the Z-axis
    # rotation = current_direction.rotation_difference(direction)
    
    axis = Vector((0, 0, 1))  # Z-axis
    angle = radians(90)  # Convert degrees to radians

    # Create a rotation matrix
    rotation_matrix = Vector((0, 0, 1)).rotation_difference(direction).to_matrix().to_4x4()
    rotation_matrix = Matrix.Rotation(angle, 4, axis) @ rotation_matrix
    
    v1 = rotation_matrix @ v1
    v2 = rotation_matrix @ v2
    v3 = rotation_matrix @ v3
    v4 = rotation_matrix @ v4
    
    v1 += pos
    v2 += pos
    v3 += pos
    v4 += pos

    leaf_verts.extend([v1, v2, v3, v4])
    start_index = len(leaf_verts) - 4
    # Faces for the two quads
    leaf_faces.append((start_index + 0, start_index + 1, start_index + 2, start_index + 3))

  # Assign vertices and faces to the leaf mesh
  leaf_mesh.from_pydata(leaf_verts, [], leaf_faces)
  leaf_mesh.update()
  uv_layer = leaf_mesh.uv_layers.new(name="UVMap")
  uv_data = uv_layer.data
  for face in leaf_mesh.polygons:
    for loop_index, uv in zip(range(face.loop_start, face.loop_start + face.loop_total), [(0, 0), (1, 0), (1, 1), (0, 1)]):
      uv_data[loop_index].uv = uv
      
  # Create a new material
  mat = bpy.data.materials.new(name="LeafMaterial")
  mat.use_nodes = True
  bsdf = mat.node_tree.nodes.get("Principled BSDF")

  # Load image
  image_path = "C:/Users/anton/Documents/Uni/Spatial Data Analysis/Procedual_Blender_Forest_Simulator/textures/chestnut_summer_color.png"  # Replace with your image path
  image = bpy.data.images.load(image_path)

  # Create texture node
  tex_image = mat.node_tree.nodes.new('ShaderNodeTexImage')
  tex_image.image = image
  
  # Connect the texture to the base color
  mat.node_tree.links.new(bsdf.inputs['Base Color'], tex_image.outputs['Color'])
  
  # Create a new object for the leaves
  leaf_obj = bpy.data.objects.new("Leaves", leaf_mesh)
  
  if leaf_obj.data.materials:
    leaf_obj.data.materials[0] = mat
  else:
    leaf_obj.data.materials.append(mat)

  # Link the leaf object to the same collection as obj_new
  bpy.context.view_layer.active_layer_collection.collection.objects.link(leaf_obj)

  # Parent the leaves to the tree object
  leaf_obj.parent = obj_new

def segmentIntoTrunkAndBranch(tree, obj_new, radii):
  top = find_top_of_trunk(tree.branchpoints)
      
  trunk_nodes = [top]
  trunk_indices = [top.index]

  while trunk_nodes[-1].parent is not None:  
    trunk_nodes.append(tree.branchpoints[trunk_nodes[-1].parent])
    trunk_indices.append(trunk_nodes[-1].index)
    

  trunk_node_positions = [trunk_node.v for trunk_node in trunk_nodes]
  branch_node_positions = [bp.v for bp in tree.branchpoints if bp not in trunk_nodes and bp.apex is not None]
  leave_nodes = [bp for bp in tree.branchpoints if bp not in trunk_nodes and bp.apex is None]
  branch_node_indices = [i for i in range(len(tree.branchpoints)) if i not in trunk_indices]

  trunk_material = create_material("TrunkMaterial", (0.77, 0.64, 0.52, 1), 2) # light brown
  branch_material = create_material("BranchMaterial", (0.36, 0.25, 0.20, 1), 3) # dark brown
  assign_material(obj_new, trunk_material)
  assign_material(obj_new, branch_material)
  trunk_vertex_indices = []
  branch_vertex_indices = []
  
  # maybe unnecessary
  bpy.context.view_layer.objects.active = obj_new
  obj_new.select_set(True)
  
  bpy.ops.object.modifier_apply(modifier="Subdivision")
  
  # obj_new.data = final_mesh

  trunk_node_kd_tree = KDTree(trunk_node_positions)
  branch_node_kd_tree = KDTree(branch_node_positions)
  for poly in obj_new.data.polygons:
    position = poly.center
    trunk_node_distance, trunk_node_index = trunk_node_kd_tree.query(position, 1)
    branch_node_distance, branch_node_index = branch_node_kd_tree.query(position, 1)
    
    if trunk_node_distance - radii[trunk_indices[trunk_node_index]] < branch_node_distance - radii[branch_node_indices[branch_node_index]]:
      poly.material_index = 0
    else:
      poly.material_index = 1
    
  assign_vertices_to_group(obj_new, "TrunkGroup", trunk_vertex_indices)
  assign_vertices_to_group(obj_new, "BranchGroup", branch_vertex_indices)
  
  
  # add_leaves_to_tree(tree, leave_nodes, obj_processed)
  
  obj_new.data.update()
  
  return obj_new

def create_inverse_graph(branchpoints):
  node_to_children = {}
  for bp in branchpoints:
    if bp.parent is not None:
      if bp.parent not in node_to_children:
        node_to_children[bp.parent] = []
      node_to_children[bp.parent].append(bp.index)
  return node_to_children

def find_top_of_trunk(branchpoints):
  node_to_children = create_inverse_graph(branchpoints)
  candidate = branchpoints[0]
  queue = node_to_children[0]
  while len(queue) > 0:
    current_index = queue.pop(0)
    current_node = branchpoints[current_index]
    if (current_node.parent is not None 
        and branchpoints[current_node.parent].shoot != current_node):
      if current_node.connections < candidate.connections:
        candidate = current_node
      queue.extend(node_to_children.get(current_index, []))
  return candidate

def create_material(name, color, pass_index):
  mat = bpy.data.materials.get(name)
  if mat is None:
    mat = bpy.data.materials.new(name=name)
    mat.diffuse_color = color
    mat.pass_index = pass_index
  else:
    mat.diffuse_color = color
    mat.pass_index = pass_index
    
  return mat

def assign_material(obj, mat):
  if mat.name not in obj.data.materials:
    obj.data.materials.append(mat)

def assign_vertices_to_group(obj, group_name, vertex_indices):
  if group_name not in obj.vertex_groups:
    group = obj.vertex_groups.new(name=group_name)
  else:
    group = obj.vertex_groups[group_name]
  group.add(vertex_indices, 1.0, 'ADD')

class SCATree():

  def __init__(self, 
              class_id=0,
              interNodeLength=0.25,
              killDistance=0.1,
              influenceRange=15.,
              tropism=0.,
              power=0.3,
              scale=0.01,
              useGroups=False,
              crownGroup='None',
              shadowGroup='None',
              shadowDensity=0.5,
              exclusionGroup='None',
              useTrunkGroup=False,
              trunkGroup=None,
              crownSize=5.,
              crownShape=1.,
              crownOffset=3.,
              surfaceBias=1.,
              topBias=1.,
              randomSeed=0,
              maxIterations=40,
              pruningGen=0,
              numberOfEndpoints=100,
              leaf_density=[1.0, 1.0],
              newEndPointsPer1000=0,
              maxTime=0.0,
              bLeaf=4.0,
              addLeaves=False,
              emitterScale=0.01,
              noModifiers=True,
              subSurface=False,
              showMarkers=False,
              markerScale=0.05,
              timePerformance=False,
              apicalcontrol=0.0,
              apicalcontrolfalloff=1.0,
              apicalcontroltiming=10,
              context=None,
              ):
    self.class_id = class_id
    self.internodeLength = interNodeLength
    self.killDistance = killDistance
    self.influenceRange = influenceRange
    self.tropism = tropism
    self.power = power
    self.scale = scale
    self.useGroups = useGroups
    self.crownGroup = crownGroup
    self.shadowGroup = shadowGroup
    self.shadowDensity = shadowDensity
    self.exclusionGroup = exclusionGroup
    self.useTrunkGroup = useTrunkGroup
    self.trunkGroup = trunkGroup
    self.crownSize = crownSize
    self.crownShape = crownShape
    self.crownOffset = crownOffset
    self.surfaceBias = surfaceBias
    self.topBias = topBias
    self.randomSeed = randomSeed
    self.maxIterations = maxIterations
    self.pruningGen = pruningGen
    self.numberOfEndpoints = numberOfEndpoints
    if len(leaf_density) != 2:
      raise ValueError("leaf_density must be a list of two floats, e.g. [1.0, 1.0]")
    leaf_density = [min(leaf_density), max(leaf_density)]
    leaf_density = [max(leaf_density[0], 0.0), min(leaf_density[1], 1.0)]
    self.leaf_density = leaf_density
    self.newEndPointsPer1000 = newEndPointsPer1000
    self.maxTime = maxTime
    self.bLeaf = bLeaf
    self.addLeaves = addLeaves
    self.addLeaves = True
    
    # self.objectParticles = availableParticleSettings(self, context)[0]
    self.emitterScale = emitterScale
    # self.barMaterial = availableBarkMaterials(self, context)[0]
    self.updateTree = False
    self.noModifiers = noModifiers
    self.subSurface = subSurface
    # self.skinMethod = ('NATIVE','Space tree','Spacetrees own skinning method',1)
    self.skinMethod = 'NATIVE'
    self.showMarkers = showMarkers
    self.markerScale = markerScale
    self.timePerformance = timePerformance
    self.apicalcontrol = apicalcontrol   
    self.apicalcontrolfalloff = apicalcontrolfalloff 
    self.apicalcontroltiming = apicalcontroltiming    

  def create_tree(self, context):
    # if not self.updateTree:
    #     return {'PASS_THROUGH'}
      
              
    # we load this library matrial unconditionally, i.e. each time we execute() which sounds like a waste
    # but library loads get undone as well if we redo the operator ...
    global barkmaterials
    barkmaterials = load_materials_from_bundled_lib('Procedual_Blender_Forest_Simulator', 'material_lib.blend', 'Bark')

    #bpy.types.MESH_OT_sca_tree.barkmaterials = barkmaterials
        
    # we *must* execute this every time because this operator has UNDO as attribute so anything that's changed will be reverted on each execution. If we initialize this only once, the operator crashes Blender because it will refer to stale data.
    particlesettings = load_particlesettings_from_bundled_lib('Procedual_Blender_Forest_Simulator', 'material_lib.blend', 'LeafEmitter')
    bpy.types.MESH_OT_forest_generator.particlesettings = particlesettings
      
    self.leafParticles = availableParticleSettings(self, context, particlesettings)[9]

    timings=Timer()     
      
    # necessary otherwise ray casts toward these objects may fail. However if nothing is selected, we get a runtime error ...
    # and if an object is selected that has no edit mode (e.g. an empty) we get a type error
    try:
      bpy.ops.object.mode_set(mode='EDIT', toggle=False)
      bpy.ops.object.mode_set(mode='OBJECT', toggle=False)
    except RuntimeError:
      pass
    except TypeError:
      pass
      

    if self.useGroups:
      size,minp = groupExtends(self.crownGroup)
      # volumefie=partial(groupdistribution,self.crownGroup,self.shadowGroup,self.shadowDensity,self.randomSeed,size,minp-bpy.context.scene.cursor.location)
      volumefie=partial(surface_based_groupdistribution,crowngroup=self.crownGroup,seed=self.randomSeed,size=size,pointrelativetocursor=minp-bpy.context.scene.cursor.location)
    else:
      volumefie=partial(ellipsoid2,self.crownSize*self.crownShape,self.crownSize,Vector((0,0,self.crownSize+self.crownOffset)),self.surfaceBias,self.topBias)
      
    startingpoints = []
    if self.useTrunkGroup:
      if self.trunkGroup in bpy.data.collections :
        for ob in bpy.data.collection[self.trunkGroup].objects :
          p = ob.location - context.scene.cursor.location
          startingpoints.append(Branchpoint(p,None, 0))
      
    timings.add('scastart')
    sca = SCA(NBP = self.maxIterations,
      NENDPOINTS=self.numberOfEndpoints,
      d=self.internodeLength,
      KILLDIST=self.killDistance,
      INFLUENCE=self.influenceRange,
      SEED=self.randomSeed,
      TROPISM=self.tropism,
      volume=volumefie,
      exclude=lambda p: insidegroup(p, self.exclusionGroup),
      startingpoints=startingpoints,
      apicalcontrol=self.apicalcontrol,
      apicalcontrolfalloff=self.apicalcontrolfalloff,
      apicaltiming=self.apicalcontroltiming
    )
    timings.add('sca')
          
    sca.iterate(newendpointsper1000=self.newEndPointsPer1000,maxtime=self.maxTime)
    timings.add('iterate')
    
    if self.showMarkers:
      mesh = createMarkers(sca, self.markerScale)
      obj_markers = bpy.data.objects.new(mesh.name, mesh)
      base = bpy.context.collection.objects.link(obj_markers)
    timings.add('showmarkers')
    
    self.leafParticles = next((k for k in particlesettings.keys() if k.startswith('LeavesAbstractSummer')), 'None')
    
    obj_new=createGeometry(sca,self.power,self.scale,
      self.noModifiers, self.skinMethod, self.subSurface,
      self.bLeaf, 
      #   self.leafParticles if self.addLeaves else 'None', 
      # list(particlesettings.keys())[2],
      self.leafParticles,
      self.leaf_density,
      particlesettings if self.addLeaves else 'None',
      #   self.objectParticles if self.addLeaves else 'None',
      'None',
      self.emitterScale,
      self.timePerformance,
      self.pruningGen,
      class_id=self.class_id
    )
      
    if obj_new is None:
      return None
      
    # bpy.ops.object.material_slot_add()
    # obj_new.material_slots[-1].material = barkmaterials[self.barkMaterial]
    
    if self.showMarkers:
      obj_markers.parent = obj_new
    
    self.updateTree = False
    
    if self.timePerformance:
      timings.add('Total')
      print(timings)
    
    self.timings = timings
    
    return obj_new
