import os
from functools import reduce
from itertools import chain
from os import path
from typing import Dict, List

import bmesh
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       StringProperty)
from bpy.types import Armature, EditBone, Mesh, Object, Operator
from bpy_extras.io_utils import ExportHelper
from mathutils import Matrix, Vector

from ..xfbin_lib.xfbin.structure.br.br_nud import (NudBoneType, NudUvType,
                                                   NudVertexType)
from ..xfbin_lib.xfbin.structure.nucc import (ClumpModelGroup, CoordNode,
                                              MaterialTextureGroup,
                                              NuccChunkClump, NuccChunkCoord,
                                              NuccChunkMaterial,
                                              NuccChunkModel, NuccChunkTexture,
                                              RiggingFlag)
from ..xfbin_lib.xfbin.structure.nud import (Nud, NudMaterial,
                                             NudMaterialProperty,
                                             NudMaterialTexture, NudMesh,
                                             NudMeshGroup, NudVertex)
from ..xfbin_lib.xfbin.structure.xfbin import Xfbin
from ..xfbin_lib.xfbin.xfbin_reader import read_xfbin
from ..xfbin_lib.xfbin.xfbin_writer import write_xfbin_to_path
from .common.coordinate_converter import *
from .common.helpers import hex_str_to_int
from .panels.clump_panel import (ClumpModelGroupPropertyGroup,
                                 ClumpPropertyGroup,
                                 XfbinMaterialPropertyGroup,
                                 XfbinNutTexturePropertyGroup,
                                 XfbinTextureChunkPropertyGroup,
                                 XfbinTextureGroupPropertyGroup)
from .panels.common import BoolPropertyGroup
from .panels.nud_mesh_panel import (NudMaterialPropertyGroup,
                                    NudMaterialPropPropertyGroup,
                                    NudMaterialTexturePropertyGroup,
                                    NudMeshPropertyGroup)
from .panels.nud_panel import NudPropertyGroup


class ExportXfbin(Operator, ExportHelper):
    """Export current collection as XFBIN file"""
    bl_idname = 'export_scene.xfbin'
    bl_label = 'Export XFBIN'

    filename_ext = ''

    filter_glob: StringProperty(default='*.xfbin', options={'HIDDEN'})

    def collection_callback(self, context):
        items = set()
        active_col = bpy.context.collection

        if active_col:
            items.add((active_col.name, active_col.name, ''))

        items.update([(c.name, c.name, '') for c in bpy.data.collections])

        return list([i for i in items if i[0] != 'Collection'])

    def collection_update(self, context):
        self.meshes_to_export.clear()
        col = bpy.data.collections.get(self.collection)
        if col:
            for armature in [obj for obj in col.objects if obj.type == 'ARMATURE']:
                for empty in [obj for obj in armature.children if obj.type == 'EMPTY']:
                    self.meshes_to_export.add().name = empty.name

    collection: EnumProperty(
        items=collection_callback,
        name='Collection',
        description='The collection to be exported. All armatures in the collection will be converted and put in the same XFBIN',
        update=collection_update,
    )

    inject_to_xfbin: BoolProperty(
        name='Inject to existing XFBIN',
        description='If True, will add (or overwrite) the exportable models as pages in the selected XFBIN.\n'
        'If False, will create a new XFBIN and overwrite the old file if it exists.\n\n'
        'NOTE: If True, the selected path has to be an XFBIN file that already exists, and that file will be overwritten',
        default=True,
    )

    export_meshes: BoolProperty(
        name='Export meshes',
        description='If True, will export the meshes of each armature in the collection to the XFBIN.\n'
        'If False, will NOT rebuild the meshes nor update the ones in the XFBIN.\n\n'
        'NOTE: "Inject to existing XFBIN" has to be enabled for this option to take effect',
        default=True,
    )

    export_bones: BoolProperty(
        name='Export bones',
        description='If True, will export the bones of each armature in the collection to the XFBIN.\n'
        'If False, will NOT update the bone coordinates in the XFBIN.\n\n'
        'NOTE: "Inject to existing XFBIN" has to be enabled for this option to take effect',
        default=True,
    )

    export_textures: BoolProperty(
        name='Export textures',
        description='If True, will include the NUT textures provided in the Clump properties\' "Texture Chunks" panel to the XFBIN.\n'
        'If False, will NOT export any textures, and will reuse the textures from the existing XFBIN.\n\n'
        'NOTE: "Inject to existing XFBIN" has to be enabled for this option to take effect',
        default=True,
    )

    export_specific_meshes: BoolProperty(
        name='Export specific meshes',
        description='If True, will export only the selected (NUD) models in the box below.\n'
        'If "Inject to existing XFBIN" is also enabled, the existing models will be used instead of the non-exported models.\n'
        'If False, will export all models in the collection',
        default=False,
    )

    meshes_to_export: CollectionProperty(
        type=BoolPropertyGroup,
    )

    def draw(self, context):
        layout = self.layout

        layout.use_property_split = True
        layout.use_property_decorate = True  # No animation.

        layout.prop(self, 'collection')

        # TODO: Re-enable changing these when support is added
        inject_row = layout.row()
        inject_row.prop(self, 'inject_to_xfbin')
        inject_row.enabled = False

        layout.prop(self, 'export_meshes')

        layout.prop(self, 'export_bones')

        layout.prop(self, 'export_textures')

        layout.prop(self, 'export_specific_meshes')

        if self.export_specific_meshes:
            # Update the "meshes to export" collection
            if not self.meshes_to_export:
                self.collection_update(context)

            box1 = layout.box()
            collection = bpy.data.collections.get(self.collection)

            if not collection:
                box1.label(text='No collection has been selected.')
            else:
                box1.label(text='Selected models:')
                # Draw a check box for each NUD to choose which models should be exported
                for item in self.meshes_to_export:
                    row = box1.split(factor=0.80)

                    row.label(text=item.name)
                    row.prop(item, 'value', text='')

    def execute(self, context):
        import time

        # try:
        start_time = time.time()
        exporter = XfbinExporter(self.filepath, self.as_keywords(ignore=('filter_glob',)))
        exporter.export_collection(context)

        elapsed_s = "{:.2f}s".format(time.time() - start_time)
        self.report({'INFO'}, f'Finished exporting {exporter.collection.name} in {elapsed_s}')
        return {'FINISHED'}
        # except Exception as e:
        #     print(e)
        #     self.report({'ERROR'}, str(e))
        # return {'CANCELLED'}


class XfbinExporter:
    def __init__(self, filepath, import_settings: dict):
        self.filepath = filepath
        self.collection: bpy.types.Collection = bpy.data.collections[import_settings.get('collection')]

        self.inject_to_xfbin = import_settings.get('inject_to_xfbin')

        self.export_meshes = import_settings.get('export_meshes')
        self.export_bones = import_settings.get('export_bones')
        self.export_textures = import_settings.get('export_textures')
        self.export_specific_meshes = import_settings.get('export_specific_meshes')
        self.meshes_to_export = import_settings.get('meshes_to_export')

    xfbin: Xfbin

    def export_collection(self, context):
        self.xfbin = Xfbin()
        if self.inject_to_xfbin:
            if not path.isfile(self.filepath):
                raise Exception(f'Cannot inject XFBIN - File does not exist: {self.filepath}')

            self.xfbin = read_xfbin(self.filepath)
        else:
            self.export_meshes = self.export_bones = self.export_textures = True

        for armature_obj in [obj for obj in self.collection.objects if obj.type == 'ARMATURE']:
            # Try adding each texture chunk as a page, if its path exists
            for texture_chunk in armature_obj.xfbin_clump_data.texture_chunks:
                if texture_chunk.include and texture_chunk.nut_path and os.path.isfile(texture_chunk.nut_path):
                    texture_chunk: XfbinTextureChunkPropertyGroup

                    chunk = NuccChunkTexture(texture_chunk.path, texture_chunk.texture_name)
                    with open(texture_chunk.nut_path, 'rb') as f:
                        chunk.nut_data = f.read()

                    # Sanity check
                    if not (len(chunk.nut_data) > 4 and chunk.nut_data[:4] == b'NTP3'):
                        print(
                            f'[NUT] Path for {texture_chunk.texture_name} is not a valid NUT file and will be skipped.')
                        continue

                    self.xfbin.add_chunk_page(chunk)

            self.xfbin.add_clump_page(self.make_clump(armature_obj, context))

        # Write the xfbin
        write_xfbin_to_path(self.xfbin, self.filepath)

    def make_clump(self, armature_obj: Object, context) -> NuccChunkClump:
        """Creates and returns a NuccChunkClump made from an Armature and its child meshes."""

        # Set the armature as the active object to be able to get its edit bones
        context.view_layer.objects.active = armature_obj

        armature: Armature = armature_obj.data
        empties: List[Mesh] = [obj for obj in armature_obj.children if obj.type == 'EMPTY']

        clump_data: ClumpPropertyGroup = armature_obj.xfbin_clump_data

        # Remove the added " [C]" from the clump's name if it exists
        clump = NuccChunkClump(clump_data.path, armature.name[:-4] if armature.name.endswith(' [C]') else armature.name)
        old_clump = None

        # Get the clump data properties
        clump.field00 = clump_data.field00

        clump.coord_flag0 = clump_data.coord_flag0
        clump.coord_flag1 = clump_data.coord_flag1

        clump.model_flag0 = clump_data.model_flag0
        clump.model_flag1 = clump_data.model_flag1

        if self.inject_to_xfbin:
            # Try to get the clump in the existing xfbin
            old_clump = self.xfbin.get_chunk_page(clump)

            if old_clump:
                # There should be only 1 clump per page anyway
                old_clump: NuccChunkClump = old_clump[1].get_chunks_by_type(NuccChunkClump)[0]

        if self.export_bones:
            clump.coord_chunks = self.make_coords(armature, clump, context)
        elif old_clump:
            clump.coord_chunks = old_clump.coord_chunks
        else:
            raise Exception('Cannot export bones.')

        if self.export_meshes:
            # Create the material chunks
            xfbin_mats = dict()
            for mat in clump_data.materials:
                xfbin_mats[mat.material_name] = self.make_xfbin_material(mat, clump, context)

            # Create the model chunks as a dict to make it easier to preserve order
            model_chunks = {m.name: m for m in self.make_models(empties, clump, old_clump, xfbin_mats, context)}

            # Set the model chunks and model groups based on the clump data
            clump.model_chunks = [model_chunks[c.value] for c in clump_data.models if c.value in model_chunks]

            # Add a None reference for model groups that might use it
            # Hopefully no actual models have that name...
            model_chunks['None'] = None

            # Add the model groups from the clump data
            clump.model_groups = list()
            for group in clump_data.model_groups:
                group: ClumpModelGroupPropertyGroup
                g = ClumpModelGroup()

                g.flag0 = group.flag0
                g.flag1 = group.flag1
                g.unk = hex_str_to_int(group.unk)
                g.model_chunks = [model_chunks[c.value] for c in group.models if c.value in model_chunks]

                clump.model_groups.append(g)
        elif old_clump:
            clump.model_chunks = old_clump.model_chunks
            clump.model_groups = old_clump.model_groups
        else:
            raise Exception('Cannot export meshes.')

        return clump

    def make_coords(self, armature: Armature, clump: NuccChunkClump, context) -> List[NuccChunkCoord]:
        bpy.ops.object.mode_set(mode='EDIT')

        coords: List[NuccChunkCoord] = list()

        def make_coord(bone: EditBone, coord_parent: CoordNode = None, parent_matrix: Matrix = Matrix.Identity(4)):
            coord = NuccChunkCoord(clump.filePath, bone.name)
            coord.node = CoordNode(coord)

            # Set up the node
            node = coord.node
            node.parent = coord_parent

            local_matrix: Matrix = parent_matrix.inverted() @ bone.matrix
            pos, _, sca = local_matrix.decompose()  # Rotation should be converted from the matrix directly

            # Apply the scale signs if they exist
            scale_signs = bone.get('scale_signs')
            if scale_signs is not None:
                sca *= Vector(scale_signs)

            # Set the coordinates of the node
            node.position = pos_m_to_cm(pos)
            node.rotation = rot_from_blender(local_matrix.to_euler('ZYX'))
            node.scale = sca[:]

            # Set the unknown values if they were imported
            unk_float = bone.get('unk_float')
            unk_short = bone.get('unk_short')
            if unk_float is not None:
                node.unkFloat = unk_float
            if unk_short is not None:
                node.unkShort = unk_short

            # Add the coord chunk to the list
            coords.append(coord)

            # Recursively add all children of each bone
            for c in bone.children:
                make_coord(c, node, bone.matrix)

        # Iterate through the root bones to process their children in order
        for root_bone in [b for b in armature.edit_bones if b.parent is None]:
            make_coord(root_bone)

        for coord in coords:
            if coord.node.parent:
                coord.node.parent.children.append(coord.node)

        bpy.ops.object.mode_set(mode='OBJECT')

        return coords

    def make_models(self, empties: List[Object], clump: NuccChunkClump, old_clump: NuccChunkClump, xfbin_mats: Dict[str, NuccChunkMaterial], context) -> List[NuccChunkModel]:
        model_chunks = list()

        coord_indices_dict = {
            name: i
            for i, name in enumerate(tuple(map(lambda c: c.name, clump.coord_chunks)))
        }

        # Get a list of all models in from the old clump
        old_clump_all_models = list(dict.fromkeys(
            chain(old_clump.model_chunks, *old_clump.model_groups))) if old_clump else None

        for empty in empties:
            if self.export_specific_meshes:
                # Use existing models from the old clump if the current model is not supposed to be exported
                mesh_index = self.meshes_to_export.find(empty.name)
                if mesh_index == -1:
                    continue

                if self.meshes_to_export[mesh_index].value is False:
                    if old_clump:
                        old_model = [c for c in old_clump_all_models if c and c.name == empty.name]
                        if old_model:
                            model_chunks.append(old_model[0])
                    continue

            nud_data: NudPropertyGroup = empty.xfbin_nud_data
            # Create the chunk and set its properties
            chunk = NuccChunkModel(clump.filePath, empty.name)
            chunk.clump_chunk = clump

            # Get the index of the mesh bone of this model
            chunk.coord_index = coord_indices_dict.get(nud_data.mesh_bone, 0)

            chunk.material_chunks = list()

            # Reduce the set of flags to a single flag
            chunk.rigging_flag = RiggingFlag(reduce(lambda x, y: int(x) |
                                                    int(y), nud_data.rigging_flag.union(nud_data.rigging_flag_extra), 0))

            chunk.material_flags = list(nud_data.material_flags)
            chunk.flag1_floats = list(nud_data.flag1_floats) if nud_data.material_flags[1] & 0x04 else tuple()

            # Create the nud
            chunk.nud = Nud()
            chunk.nud.name = chunk.name

            # Set the nud's properties
            chunk.nud.bounding_sphere = pos_m_to_cm_tuple(nud_data.bounding_sphere_nud)

            # Always treat nuds as having only 1 mesh group
            chunk.nud.mesh_groups = [NudMeshGroup()]
            mesh_group = chunk.nud.mesh_groups[0]
            mesh_group.name = chunk.name

            mesh_group.bone_flags = nud_data.bone_flag
            mesh_group.bounding_sphere = pos_m_to_cm_tuple(nud_data.bounding_sphere_group)

            mesh_group.meshes = list()

            # Get the armature's data
            armature: Armature = empty.parent.data
            mesh_bone = armature.bones.get(nud_data.mesh_bone)
            empty_parent_type = empty.parent_type

            for mesh_obj in [c for c in empty.children if c.type == 'MESH']:
                nud_mesh = NudMesh()
                vertices = nud_mesh.vertices = list()
                faces = nud_mesh.faces = list()

                # Generate a mesh with modifiers applied, and put it into a bmesh
                mesh = mesh_obj.evaluated_get(context.evaluated_depsgraph_get()).data

                # Transform the mesh by the inverse of its bone's matrix, if it was not parented to it
                if mesh_bone and empty_parent_type != 'BONE':
                    mesh.transform(mesh_bone.matrix_local.to_4x4().inverted())

                bm = bmesh.new()
                bm.from_mesh(mesh)
                bm.verts.ensure_lookup_table()
                bm.verts.index_update()

                # Get the current bone weighting layer
                deform_layer = bm.verts.layers.deform.active
                v_groups = mesh_obj.vertex_groups

                color_layer = None
                if len(bm.loops.layers.color):
                    color_layer = bm.loops.layers.color[0]

                uv_layer1 = None
                uv_layer2 = None
                if len(bm.loops.layers.uv):
                    uv_layer1 = bm.loops.layers.uv[0]
                if len(bm.loops.layers.uv) > 1:
                    uv_layer2 = bm.loops.layers.uv[1]

                # Loop vertex index -> nud vertex index
                vertex_indices_dict = dict()

                for tri_loops in bm.calc_loop_triangles():
                    for l in tri_loops:
                        # TODO: Check for non-smooth loops
                        if l.vert.index not in vertex_indices_dict:
                            # Add this index as a key to the dictionary
                            vertex_indices_dict[l.vert.index] = len(vertex_indices_dict)

                            # Create and add the vertex
                            vert = NudVertex()
                            vertices.append(vert)

                            # Position and normal, tangent, bitangent
                            vert.position = pos_scaled_from_blender(l.vert.co.xyz)
                            vert.normal = pos_from_blender(l.vert.normal.xyz)  # l.calc_normal().xyz
                            vert.tangent = pos_from_blender(l.calc_tangent().xyz)
                            vert.bitangent = Vector(vert.normal).cross(Vector(vert.tangent))

                            # Color
                            vert.color = tuple()
                            if color_layer:
                                vert.color = tuple(map(lambda x: int(x * 255), l[color_layer]))

                            # UV
                            vert.uv = list()
                            if uv_layer1:
                                vert.uv.append(uv_from_blender(l[uv_layer1].uv))
                            if uv_layer2:
                                vert.uv.append(uv_from_blender(l[uv_layer2].uv))

                            vert.bone_ids = tuple()
                            vert.bone_weights = tuple()
                            if deform_layer:
                                # Bone indices and weights
                                # Direct copy of TheTurboTurnip's weight sorting method
                                # https://github.com/theturboturnip/yk_gmd_io/blob/master/yk_gmd_blender/blender/export/legacy/exporter.py#L302-L316

                                # Get a list of (vertex group ID, weight) items sorted in descending order of weight
                                # Take the top 4 elements, for the top 4 most deforming bones
                                # Normalize the weights so they sum to 1
                                b_weights = [(v_groups[b].name, w) for b, w in sorted(l.vert[deform_layer].items(),
                                                                                      key=lambda i: 1 - i[1]) if v_groups[b].name in coord_indices_dict]
                                if len(b_weights) > 4:
                                    b_weights = b_weights[:4]
                                elif len(b_weights) < 4:
                                    # Add zeroed elements to b_weights so it's 4 elements long
                                    b_weights += [(0, 0.0)] * (4 - len(b_weights))

                                weight_sum = sum(weight for (_, weight) in b_weights)
                                if weight_sum > 0.0:
                                    vert.bone_ids = tuple(map(lambda bw: coord_indices_dict.get(bw[0], 0), b_weights))
                                    vert.bone_weights = tuple(map(lambda bw: bw[1] / weight_sum, b_weights))
                                else:
                                    vert.bone_ids = [0] * 4
                                    vert.bone_weights = [0] * 3 + [1]

                    # Get the vertex indices to make the face
                    faces.append(tuple(map(lambda l: vertex_indices_dict[l.vert.index], tri_loops)))

                if len(vertices) < 3:
                    print(f'[NUD MESH] {mesh_obj.name} has no valid faces and will be skipped.')
                    continue

                if len(vertices) > NudMesh.MAX_VERTICES:
                    print(
                        f'[NUD MESH] {mesh_obj.name} has {len(vertices)} vertices (limit is {NudMesh.MAX_VERTICES}) and will be skipped.')
                    continue

                if len(faces) > NudMesh.MAX_FACES:
                    print(
                        f'[NUD MESH] {mesh_obj.name} has {len(bm.faces)} faces (limit is {NudMesh.MAX_FACES}) and will be skipped.')
                    continue

                mesh_data: NudMeshPropertyGroup = mesh_obj.xfbin_mesh_data

                # Get the vertex/bone/uv formats from the mesh property group
                nud_mesh.vertex_type = NudVertexType(int(mesh_data.vertex_type))
                nud_mesh.bone_type = NudBoneType(int(mesh_data.bone_type))
                nud_mesh.uv_type = NudUvType(int(mesh_data.uv_type))
                nud_mesh.face_flag = mesh_data.face_flag

                # Add the material chunk for this mesh
                mat = xfbin_mats.get(mesh_data.xfbin_material)
                if mat is None:
                    print(
                        f'[NUD MESH] {mesh_obj.name} has a non-existing XFBIN material and will be skipped.')
                    continue

                chunk.material_chunks.append(mat)

                # Get the material properties of this mesh
                nud_mesh.materials = self.make_nud_materials(mesh_data, clump, context)

                # Only add the mesh if it doesn't exceed the vertex and face limits
                mesh_group.meshes.append(nud_mesh)

                # Free the mesh after we're done with it
                bm.free()

            if not mesh_group.meshes:
                print(f'[NUD] {empty.name} does not contain any exported meshes and will be skipped.')
                continue

            # Only add the model chunk if its NUD contains at least one mesh
            model_chunks.append(chunk)

        return model_chunks

    def make_nud_materials(self, pg: NudMeshPropertyGroup, clump: NuccChunkClump, context) -> List[NudMaterial]:
        materials = list()

        # There is a maximum of 4 materials per mesh
        for mat in pg.materials[:4]:
            mat: NudMaterialPropertyGroup
            m = NudMaterial()
            m.flags = hex_str_to_int(mat.material_id)

            m.sourceFactor = mat.source_factor
            m.destFactor = mat.dest_factor
            m.alphaTest = mat.alpha_test
            m.alphaFunction = mat.alpha_function
            m.refAlpha = mat.ref_alpha
            m.cullMode = mat.cull_mode
            m.unk1 = mat.unk1
            m.unk2 = mat.unk2
            m.zBufferOffset = mat.zbuffer_offset

            m.textures = list()
            for texture in mat.textures:
                texture: NudMaterialTexturePropertyGroup
                t = NudMaterialTexture()

                t.unk0 = texture.unk0
                t.mapMode = texture.map_mode
                t.wrapModeS = texture.wrap_mode_s
                t.wrapModeT = texture.wrap_mode_t
                t.minFilter = texture.min_filter
                t.magFilter = texture.mag_filter
                t.mipDetail = texture.mip_detail
                t.unk1 = texture.unk1
                t.unk2 = texture.unk2

                m.textures.append(t)

            m.properties = list()
            for prop in mat.material_props:
                prop: NudMaterialPropPropertyGroup
                p = NudMaterialProperty()
                p.name = prop.prop_name

                p.values = list()
                for i in range(prop.count):
                    p.values.append(prop.values[i].value)

                m.properties.append(p)

            materials.append(m)

        return materials

    def make_xfbin_material(self, pg: XfbinMaterialPropertyGroup, clump: NuccChunkClump, context) -> NuccChunkMaterial:
        chunk = NuccChunkMaterial(clump.filePath, pg.material_name)

        chunk.field02 = pg.field02
        chunk.field04 = pg.field04

        chunk.format = hex_str_to_int(pg.float_format)
        chunk.floats = list(pg.floats)[:NuccChunkMaterial.float_count(chunk.format)]

        chunk.texture_groups = list()
        for group in pg.texture_groups:
            group: XfbinTextureGroupPropertyGroup
            g = MaterialTextureGroup()
            g.unk = group.flag

            g.texture_chunks = list()
            for texture in group.textures:
                texture: XfbinNutTexturePropertyGroup
                t = NuccChunkTexture(texture.path, texture.texture_name)

                if self.export_textures:
                    # TODO: Export textures
                    # Might want to merge XfbinTextureChunkPropertyGroup with XfbinNutTexturePropertyGroup
                    # when adding support for texture exporting
                    pass

                g.texture_chunks.append(t)
            chunk.texture_groups.append(g)

        return chunk


def menu_func_export(self, context):
    self.layout.operator(ExportXfbin.bl_idname, text='CyberConnect2 Model Container (.xfbin)')
