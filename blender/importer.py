import os
from itertools import chain
from typing import List

import bmesh
import bpy
from bmesh.types import BMesh
from bpy.props import BoolProperty, StringProperty
from bpy.types import Bone, Material, Object, Operator
from bpy_extras.io_utils import ImportHelper
from mathutils import Matrix, Vector

from ..xfbin_lib.xfbin.structure.nucc import (CoordNode, NuccChunkClump,
                                              NuccChunkModel, NuccChunkTexture)
from ..xfbin_lib.xfbin.structure.nud import NudMesh
from ..xfbin_lib.xfbin.structure.xfbin import Xfbin
from ..xfbin_lib.xfbin.xfbin_reader import read_xfbin
from .common.coordinate_converter import *
from .common.helpers import XFBIN_TEXTURES_OBJ
from .panels.clump_panel import XfbinMaterialPropertyGroup


class ImportXFBIN(Operator, ImportHelper):
    """Loads an XFBIN file into blender"""
    bl_idname = "import_scene.xfbin"
    bl_label = "Import XFBIN"

    use_full_material_names: BoolProperty(
        name="Full material names",
        description="Display full name of materials in NUD meshes, instead of a shortened form")

    filter_glob: StringProperty(default="*.xfbin", options={"HIDDEN"})

    def draw(self, context):
        layout = self.layout

        layout.use_property_split = True
        layout.use_property_decorate = True

        layout.prop(self, 'use_full_material_names')

    def execute(self, context):
        import time

        # try:
        start_time = time.time()
        importer = XfbinImporter(self, self.filepath, self.as_keywords(ignore=("filter_glob",)))

        importer.read(context)

        elapsed_s = "{:.2f}s".format(time.time() - start_time)
        print("XFBIN import finished in " + elapsed_s)

        return {'FINISHED'}
        # except Exception as error:
        #     print("Catching Error")
        #     self.report({"ERROR"}, str(error))
        # return {'CANCELLED'}


class XfbinImporter:
    def __init__(self, operator: Operator, filepath: str, import_settings: dict):
        self.operator = operator
        self.filepath = filepath
        self.use_full_material_names = import_settings.get("use_full_material_names")

    xfbin: Xfbin
    collection: bpy.types.Collection

    def read(self, context):
        self.xfbin = read_xfbin(self.filepath)
        self.collection = self.make_collection(context)

        texture_chunks: List[NuccChunkTexture] = list()

        for page in self.xfbin.pages:
            # Add all texture chunks inside the xfbin
            texture_chunks.extend(page.get_chunks_by_type('nuccChunkTexture'))

            clump = page.get_chunks_by_type('nuccChunkClump')

            if not len(clump):
                continue

            clump: NuccChunkClump = clump[0]

            # Clear unsupported chunks to avoid issues
            if clump.clear_non_model_chunks() > 0:
                self.operator.report(
                    {'WARNING'}, f'Some chunks in {clump.name} have unsupported types and will not be imported')

            armature_obj = self.make_armature(clump, context)
            self.make_objects(clump, armature_obj, context)

            # Set the armature as the active object after importing everything
            bpy.ops.object.mode_set(mode='OBJECT')
            context.view_layer.objects.active = armature_obj

            # Update the models' PointerProperty to use the models that were just imported
            armature_obj.xfbin_clump_data.update_models(armature_obj)

        # Create an empty object to store the texture chunks list
        empty = bpy.data.objects.new(f'{XFBIN_TEXTURES_OBJ} [{self.collection.name}]', None)
        empty.empty_display_size = 0

        # Link the empty to the collection
        self.collection.objects.link(empty)

        # Add the found texture chunks to the empty object
        empty.xfbin_texture_chunks_data.init_data(texture_chunks)

    def make_collection(self, context) -> bpy.types.Collection:
        """
        Build a collection to hold all of the objects and meshes from the GMDScene.
        :param context: The context used by the import process.
        :return: A collection which the importer can add objects and meshes to.
        """

        collection_name = os.path.basename(self.filepath).split('.')[0]
        collection = bpy.data.collections.new(collection_name)
        # Link the new collection to the currently active collection.
        context.collection.children.link(collection)
        return collection

    def make_armature(self, clump: NuccChunkClump, context):
        armature_name = f'{clump.name} [C]'  # Avoid blender renaming meshes by making the armature name unique

        armature = bpy.data.armatures.new(f"{armature_name}")
        armature.display_type = 'STICK'

        armature_obj = bpy.data.objects.new(f"{armature_name}", armature)
        armature_obj.show_in_front = True

        # Set the Xfbin clump properties
        armature_obj.xfbin_clump_data.init_data(clump)

        self.collection.objects.link(armature_obj)

        context.view_layer.objects.active = armature_obj
        bpy.ops.object.mode_set(mode='EDIT')

        bone_matrices = dict()

        def make_bone(node: CoordNode):

            # Find the local->world matrix for the parent bone, and use this to find the local->world matrix for the current bone
            if node.parent:
                parent_matrix = bone_matrices[node.parent.name]
            else:
                parent_matrix = Matrix.Identity(4)

            # Convert the node values
            pos = pos_cm_to_m(node.position)
            rot = rot_to_blender(node.rotation)
            sca = Vector(tuple(map(lambda x: abs(x), node.scale)))  # Absolute value of the scale

            # Set up the transformation matrix
            this_bone_matrix = parent_matrix @ (Matrix.Translation(pos) @
                                                rot.to_matrix().to_4x4() @ Matrix.Diagonal(sca).to_4x4())

            # Add the matrix to the dictionary
            bone_matrices[node.name] = this_bone_matrix

            bone = armature.edit_bones.new(node.name)
            bone.use_relative_parent = False
            bone.use_deform = True

            # Having a long tail would offset the meshes parented to the mesh bones, so we avoid that for now
            bone.tail = Vector((0, 0.0001, 0))

            bone.matrix = this_bone_matrix
            bone.parent = armature.edit_bones[node.parent.name] if node.parent else None

            # Store the signs of the node's scale to apply when exporting, as applying them here (if negative) will break the rotation
            bone['scale_signs'] = tuple(map(lambda x: -1 if x < 0 else 1, node.scale))

            # Store these unknown values to set when exporting
            bone['unk_float'] = node.unkFloat
            bone['unk_short'] = node.unkShort

            for child in node.children:
                make_bone(child)

        for root in clump.root_nodes:
            make_bone(root)

        bpy.ops.object.mode_set(mode='OBJECT')

        return armature_obj

    def make_objects(self, clump: NuccChunkClump, armature_obj: Object, context):
        vertex_group_list = [coord.node.name for coord in clump.coord_chunks]
        vertex_group_indices = {
            name: i
            for i, name in enumerate(vertex_group_list)
        }

        # Small QoL fix for JoJo "_f" models to show shortened material names
        clump_name = clump.name
        if clump_name.endswith('_f'):
            clump_name = clump_name[:-2]

        # Create a blender material for each xfbin material chunk
        xfbin_material_dict = {
            name: mat
            for name, mat in map(lambda x: (x.name, self.make_material(x)), armature_obj.xfbin_clump_data.materials)
        }

        all_model_chunks = list(dict.fromkeys(
            chain(clump.model_chunks, *map(lambda x: x.model_chunks, clump.model_groups))))

        for nucc_model in all_model_chunks:
            if not (isinstance(nucc_model, NuccChunkModel) and nucc_model.nud):
                continue

            nud = nucc_model.nud

            # Create an empty to store the NUD's properties, and set the armature to be its parent
            empty = bpy.data.objects.new(nucc_model.name, None)
            empty.empty_display_size = 0
            empty.parent = armature_obj

            # Link the empty to the collection
            self.collection.objects.link(empty)

            # Set the NUD properties
            empty.xfbin_nud_data.init_data(nucc_model, nucc_model.coord_chunk.name if nucc_model.coord_chunk else None)

            # Get the bone range that this NUD uses
            bone_range = nud.get_bone_range()

            # Set the mesh bone as the empty's parent bone, if it exists (it should)
            mesh_bone = None
            if nucc_model.coord_chunk:
                mesh_bone: Bone = armature_obj.data.bones.get(nucc_model.coord_chunk.name)
                if mesh_bone and bone_range == (0, 0):
                    # Parent to bone ONLY if the mesh doesn't have any other bones weighted to it (teeth for example)
                    empty.parent_bone = mesh_bone.name
                    empty.parent_type = 'BONE'

            for group in nud.mesh_groups:
                for i, mesh in enumerate(group.meshes):
                    mat_chunk = nucc_model.material_chunks[i]
                    mat_name = mat_chunk.name

                    # Try to shorten the material name before adding it to the mesh name
                    if (not self.use_full_material_names) and mat_name.startswith(clump_name):
                        mat_name = mat_name[len(clump_name):].strip(' _')

                    # Add the material name to the group name because we don't have a way
                    # to differentiate between meshes in the same group
                    # The order of the mesh might matter, so the index is added here regardless
                    mesh_name = f'{group.name} ({i+1}) [{mat_name}]' if len(mat_name) else group.name

                    overall_mesh = bpy.data.meshes.new(mesh_name)

                    # This list will get filled in nud_mesh_to_bmesh
                    custom_normals = list()
                    new_bmesh = self.nud_mesh_to_bmesh(mesh, clump, vertex_group_indices, custom_normals)

                    # Convert the BMesh to a blender Mesh
                    new_bmesh.to_mesh(overall_mesh)
                    new_bmesh.free()

                    # Use the custom normals we made eariler
                    overall_mesh.create_normals_split()
                    overall_mesh.normals_split_custom_set_from_vertices(custom_normals)
                    overall_mesh.auto_smooth_angle = 0
                    overall_mesh.use_auto_smooth = True

                    # If we're not going to parent it, transform the mesh by the bone's matrix
                    if mesh_bone and bone_range != (0, 0):
                        overall_mesh.transform(mesh_bone.matrix_local.to_4x4())

                    # Add the xfbin material to the mesh
                    overall_mesh.materials.append(xfbin_material_dict.get(mat_chunk.name))

                    mesh_obj: bpy.types.Object = bpy.data.objects.new(mesh_name, overall_mesh)

                    # Link the mesh object to the collection
                    self.collection.objects.link(mesh_obj)

                    # Parent the mesh to the empty
                    mesh_obj.parent = empty

                    # Set the mesh as the active object to properly initialize its PropertyGroup
                    context.view_layer.objects.active = mesh_obj

                    # Set the NUD mesh properties
                    mesh_obj.xfbin_mesh_data.init_data(mesh, mat_chunk.name)

                    # Create the vertex groups for all bones (required)
                    for name in [coord.node.name for coord in clump.coord_chunks]:
                        mesh_obj.vertex_groups.new(name=name)

                    # Apply the armature modifier
                    modifier = mesh_obj.modifiers.new(type='ARMATURE', name="Armature")
                    modifier.object = armature_obj

    def make_material(self, xfbin_mat: XfbinMaterialPropertyGroup) -> Material:
        material: Material = bpy.data.materials.new(f'[XFBIN] {xfbin_mat.name}')

        if xfbin_mat.texture_groups and xfbin_mat.texture_groups[0].textures:
            image_name = xfbin_mat.texture_groups[0].textures[0].texture

            material.use_nodes = True
            bsdf_node = material.node_tree.nodes.get('Principled BSDF')

            image_node = material.node_tree.nodes.new('ShaderNodeTexImage')
            image_node.location = (-300, 220)

            # Try different name variations because blender loads external images with their extension
            for name in (image_name, f'{image_name}.dds', f'{image_name}.png'):
                image_node.image = bpy.data.images.get(name)

                if image_node.image is not None:
                    # If the image exists, link it to the material
                    material.node_tree.links.new(image_node.outputs['Color'], bsdf_node.inputs['Base Color'])
                    break

            # Remove the image node if we couldn't find the texture
            if image_node.image is None:
                material.node_tree.nodes.remove(image_node)

        return material

    def nud_mesh_to_bmesh(self, mesh: NudMesh, clump: NuccChunkClump, vertex_group_indices, custom_normals) -> BMesh:
        bm = bmesh.new()

        deform = bm.verts.layers.deform.new("Vertex Weights")

        # Vertices
        for i in range(len(mesh.vertices)):
            vtx = mesh.vertices[i]
            vert = bm.verts.new(pos_scaled_to_blender(vtx.position))

            # Tangents cannot be applied
            if vtx.normal:
                normal = pos_to_blender(vtx.normal)
                custom_normals.append(normal)
                vert.normal = normal

            if vtx.bone_weights:
                for bone_id, bone_weight in zip(vtx.bone_ids, vtx.bone_weights):
                    if bone_weight > 0:
                        vertex_group_index = vertex_group_indices[clump.coord_chunks[bone_id].name]
                        vert[deform][vertex_group_index] = bone_weight

        # Set up the indexing table inside the bmesh so lookups work
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()

        # For each triangle, add it to the bmesh
        for mesh_face in mesh.faces:
            tri_idxs = mesh_face

            # Skip "degenerate" triangles
            if len(set(tri_idxs)) != 3:
                continue

            try:
                face = bm.faces.new((bm.verts[tri_idxs[0]], bm.verts[tri_idxs[1]], bm.verts[tri_idxs[2]]))
                face.smooth = True
            except Exception as e:
                # We might get duplicate faces for some reason
                # print(e)
                pass

        # Color
        if len(mesh.vertices) and mesh.vertices[0].color:
            col_layer = bm.loops.layers.color.new("Color")
            for face in bm.faces:
                for loop in face.loops:
                    color = mesh.vertices[loop.vert.index].color
                    loop[col_layer] = tuple(map(lambda x: x / 255, color))

        # UVs
        if len(mesh.vertices) and mesh.vertices[0].uv:
            # We can have multiple UV channels - the first one will be set by default
            for i in range(len(mesh.vertices[0].uv)):
                uv_layer = bm.loops.layers.uv.new(f"UV_{i}")
                for face in bm.faces:
                    for loop in face.loops:
                        original_uv = mesh.vertices[loop.vert.index].uv[i]
                        loop[uv_layer].uv = uv_to_blender(original_uv)

        return bm


def menu_func_import(self, context):
    self.layout.operator(ImportXFBIN.bl_idname, text='XFBIN Model Container (.xfbin)')
