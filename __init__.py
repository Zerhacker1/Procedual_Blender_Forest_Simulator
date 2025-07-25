import sys

sys.path.append("C:\\users\\anton\\appdata\\roaming\\python\\python311\\site-packages")

import time
from typing import Any, List, Dict
import random
import csv
import json
from scipy.spatial import KDTree
import numpy as np

import bpy
from mathutils import Vector,Euler,Matrix,Quaternion
from .voxel_grid import VoxelGrid
from .tree_mesh_generation import SCATree
import bmesh

bl_info = {
  "name": "Forest Generator",
  "author": "Anton Hackl",
  "version": (0, 2, 14),
  "blender": (2, 93, 0),
  "location": "View3D > Add > Mesh",
  "description": "Adds a forest of trees created with the space colonization algorithm starting at the 3D cursor",
  "warning": "",
  "wiki_url": "https://github.com/varkenvarken/spacetree/wiki",
  "tracker_url": "",
  "category": "Add Mesh"}

class TreeConfiguration(bpy.types.PropertyGroup):
    path: bpy.props.StringProperty(
      name="Tree Configuration File", 
      description="Path to the file", 
      subtype='FILE_PATH',
      default="C:\\Users\\anton\\Documents\\Uni\\Spatial Data Analysis\\Procedual_Blender_Forest_Simulator\\tree_configs\\sphere_tree.json"  
    )
    weight: bpy.props.FloatProperty(
      name="Weight",
      description="Weight of the tree configuration",
      default=1,
      min=0,
    )

class ForestGenerator(bpy.types.Operator):
  bl_idname = "mesh.forest_generator"
  bl_label = "Forest Generator"
  bl_options = {'REGISTER', 'UNDO'}

  surface: bpy.props.StringProperty(
    name="Surface", 
    description="Path to the file", 
    subtype='FILE_PATH',
    default="C:\\Users\\anton\\Documents\\Uni\\Spatial Data Analysis\\surface.csv"
  )
  treeConfigurationCount: bpy.props.IntProperty(
    name="Number of tree configurations",
    description="Number of tree configurations",
    default=2,
    min=1,
  )
  tree_configurations: bpy.props.CollectionProperty(type=TreeConfiguration) 
  updateForest: bpy.props.BoolProperty(name="Generate Forest", default=False)

  voxel_model_related_configuration_fields = {
    "crown_width",
    "crown_height",
    "crown_offset",
    "crown_type",
    "stem_height",
    "stem_diameter",
  }
  
  @classmethod
  def poll(self, context):
    # Check if we are in object mode
    return context.mode == 'OBJECT'
  
  def update_tree_configurations(self):
    """
    Updates the tree configurations by adding or removing elements to match the desired tree configuration count.
    
    :return: None
    """
    
    current_count = len(self.tree_configurations)
    if self.treeConfigurationCount > current_count:
        for _ in range(self.treeConfigurationCount - current_count):
            self.tree_configurations.add()
    elif self.treeConfigurationCount < current_count:
        for _ in range(current_count - self.treeConfigurationCount):
            self.tree_configurations.remove(len(self.tree_configurations) - 1)
  
  def draw(self, context):
    """
    Draws the UI layout for the add-on, including generation settings and tree configurations.
    
    :param context: The context in which the UI is being drawn.
    :type context: bpy.types.Context
    :return: None
    :rtype: None
    """
    
    layout = self.layout
    col1 = layout.column()
    box = layout.box()
    box.prop(self, 'updateForest', icon='MESH_DATA')
    box.label(text="Generation Settings:")
    box.prop(self, 'surface')
    box.prop(self, 'treeConfigurationCount')

    for i, tree_config in enumerate(self.tree_configurations):
      col = box.column(align=True)
      col.scale_x = 20  # Adjust width scaling
      col.alignment = 'EXPAND'  # Expand to fit available space

      col.prop(tree_config, "path", text="Tree Config")
      col.prop(tree_config, "weight", text="Weight")
      
      col.separator()
        
  def execute(self, context):
    """
    Executes the process of procedurally generating a forest. The forest is generated based on the configuration of the operator.
    
    :param context: The Blender context in which the operator is executed.
    :type context: bpy.types.Context
    :return: A set indicating the execution status of the operator.
    :rtype: Set[str, str]
    """
    random.seed(random.randint(0, 1_000_000))
    self.update_tree_configurations()
    if not self.updateForest:
      return {'FINISHED'}
    
    surface_data = []
    if '.csv' in self.surface:
      with open(self.surface) as csvfile:
        csv_reader = csv.reader(csvfile, delimiter=',')
        for row in csv_reader:
            surface_data.append([int(value) for value in row])

    tree_configurations: List[dict[str, Any]] = []
    configuration_weights: List[float] = []
    for tree_config in self.tree_configurations:
      with open(tree_config.path) as tree_config_json:
        tree_configurations.append(json.load(tree_config_json))
        configuration_weights.append(tree_config.weight)
    
    tree_voxel_configurations = [
      {k : v for k, v in tree_configuration.items() 
        if k in self.voxel_model_related_configuration_fields} 
      for tree_configuration in tree_configurations
    ]
    
    tree_mesh_configurations = [
      {k : v for k, v in tree_configuration.items()
        if k not in self.voxel_model_related_configuration_fields}
      for tree_configuration in tree_configurations
    ]
    
    voxel_grid = VoxelGrid()
    voxel_grid.generate_forest(tree_voxel_configurations, configuration_weights, surface_data)
    generation_results = [voxel_grid.greedy_meshing(i) for i in range(len(voxel_grid.trees))]
    tree_configuration_indices = [generation_result[0] for generation_result in generation_results]
    tree_meshes = [generation_result[1] for generation_result in generation_results]
    tree_mesh_locations = KDTree([tree_mesh.location for tree_mesh in tree_meshes])
    
    rest_collection = bpy.data.collections.get("Rest")
    if not rest_collection:
      rest_collection = bpy.data.collections.new("Rest")
      bpy.context.scene.collection.children.link(rest_collection)
      
    crown_collection = bpy.data.collections.get("Crown")
    if not crown_collection:
      crown_collection = bpy.data.collections.new("Crown")
      bpy.context.scene.collection.children.link(crown_collection)
    
    exlusion_collection = bpy.data.collections.get("Exclusion")
    if not exlusion_collection:
      exlusion_collection = bpy.data.collections.new("Exclusion")
      bpy.context.scene.collection.children.link(exlusion_collection)

    for i, tree_mesh in enumerate(tree_meshes):
      material = self.create_random_material(f"Material_{i}")
      if tree_mesh.data.materials:
        tree_mesh.data.materials[0] = material
      else:
        tree_mesh.data.materials.append(material)
      rest_collection.objects.link(tree_mesh)
      
    max_crown_radius = max([tree_configuration["crown_width"] for tree_configuration in tree_voxel_configurations]) / 2
    original_cursor_location = bpy.context.scene.cursor.location.copy()
    total_time = 0.0
    for i, tree_mesh in enumerate(tree_meshes):
      start_time = time.time()
      bpy.context.view_layer.update()
      rest_collection.objects.unlink(tree_mesh)
      crown_collection.objects.link(tree_mesh)
      bpy.context.view_layer.update()
      
      max_range = max_crown_radius + tree_voxel_configurations[tree_configuration_indices[i]]["crown_width"] / 2
      
      in_range_trees = tree_mesh_locations.query_ball_point(tree_mesh.location, max_range)
      
      for tree_index in in_range_trees:
        if tree_index == i:
          continue
        rest_collection.objects.unlink(tree_meshes[tree_index])
        exlusion_collection.objects.link(tree_meshes[tree_index])
      
      tree_location = tree_mesh.location.copy()
      bpy.context.scene.cursor.location = tree_location
      bpy.context.view_layer.update()
      
      sca_tree = SCATree(
        useGroups=True,
        crownGroup="Crown",
        exclusionGroup="Exclusion",
        noModifiers=False,
        subSurface=True,
        randomSeed=random.randint(0, 1_000_000),
        context=context,
        class_id=i,
        **tree_mesh_configurations[tree_configuration_indices[i]],
      )
      
      sca_tree_mesh = sca_tree.create_tree(context)
      
      crown_collection.objects.unlink(tree_mesh)
      rest_collection.objects.link(tree_mesh)
      bpy.context.view_layer.update()
      
      if sca_tree_mesh == None:
        continue
      sca_tree_mesh.location = bpy.context.scene.cursor.location.copy()
      
      for tree_index in in_range_trees:
        if tree_index == i:
          continue
        exlusion_collection.objects.unlink(tree_meshes[tree_index])
        rest_collection.objects.link(tree_meshes[tree_index])
      
      end_time = time.time()
      elapsed_time = end_time - start_time
      total_time += elapsed_time
      print(f"{i+1} out of {len(tree_meshes)} trees generated at {tree_mesh.location} with configuration index {tree_configuration_indices[i]} in {elapsed_time:.2f} seconds")
    
    print(f"Total time for forest generation: {total_time:.2f} seconds")
    self.updateForest = False
    bpy.context.scene.cursor.location = original_cursor_location
    
    for collection_name in ['Crown', 'Exclusion', 'Rest']:
      collection = bpy.data.collections.get(collection_name)
      for obj in collection.objects:
        bpy.data.objects.remove(obj, do_unlink=True)
      bpy.data.collections.remove(collection)
    
    return {'FINISHED'}
        
  def create_random_material(self, name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs['Base Color'].default_value = (random.random(), random.random(), random.random(), 1)
    return mat
            
def menu_func(self, context):
  self.layout.operator(ForestGenerator.bl_idname, text="Generate Forest",
                                          icon='PLUGIN').updateForest = False

def register():
  bpy.utils.register_class(TreeConfiguration)
  bpy.utils.register_class(ForestGenerator)
  bpy.types.VIEW3D_MT_mesh_add.append(menu_func)


def unregister():
  bpy.types.VIEW3D_MT_mesh_add.remove(menu_func)
  bpy.utils.unregister_class(TreeConfiguration)
  bpy.utils.unregister_class(ForestGenerator)
      
if __name__ == "__main__":
  register()