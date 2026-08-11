import bpy
import json
from mathutils import Vector

SPEC = json.loads('[{"name": "Vehicle_Body", "primitive": "cube", "location": [0.0, 0.0, 1.35], "scale": [3.0, 1.35, 0.55], "material": "VehiclePaint", "color": [0.08, 0.33, 0.88, 1.0]}, {"name": "Vehicle_Cabin", "primitive": "cube", "location": [0.35, 0.0, 2.28], "scale": [1.35, 1.12, 0.48], "material": "WindowGlass", "color": [0.08, 0.55, 0.8, 1.0], "parent": "Vehicle_Body"}, {"name": "Vehicle_Wheel_01", "primitive": "cylinder", "location": [-2.25, -1.4, 0.58], "scale": [0.58, 0.58, 0.34], "rotation": [1.570796, 0.0, 0.0], "material": "Rubber", "color": [0.025, 0.03, 0.04, 1.0], "parent": "Vehicle_Body"}, {"name": "Vehicle_Wheel_02", "primitive": "cylinder", "location": [-2.25, 1.4, 0.58], "scale": [0.58, 0.58, 0.34], "rotation": [1.570796, 0.0, 0.0], "material": "Rubber", "color": [0.025, 0.03, 0.04, 1.0], "parent": "Vehicle_Body"}, {"name": "Vehicle_Wheel_03", "primitive": "cylinder", "location": [2.25, -1.4, 0.58], "scale": [0.58, 0.58, 0.34], "rotation": [1.570796, 0.0, 0.0], "material": "Rubber", "color": [0.025, 0.03, 0.04, 1.0], "parent": "Vehicle_Body"}, {"name": "Vehicle_Wheel_04", "primitive": "cylinder", "location": [2.25, 1.4, 0.58], "scale": [0.58, 0.58, 0.34], "rotation": [1.570796, 0.0, 0.0], "material": "Rubber", "color": [0.025, 0.03, 0.04, 1.0], "parent": "Vehicle_Body"}]')
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

def material(name, color):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    principled = mat.node_tree.nodes.get("Principled BSDF")
    if principled:
        principled.inputs["Base Color"].default_value = color
        principled.inputs["Roughness"].default_value = 0.36
    return mat

for item in SPEC:
    getattr(bpy.ops.mesh, {
        "cube": "primitive_cube_add",
        "uv_sphere": "primitive_uv_sphere_add",
        "cylinder": "primitive_cylinder_add",
        "cone": "primitive_cone_add",
        "torus": "primitive_torus_add",
    }[item["primitive"]])(location=item["location"])
    obj = bpy.context.active_object
    obj.name = item["name"]
    obj["infinity_benchmark_id"] = 'vehicle-tier-2'
    obj["infinity_primitive"] = item["primitive"]
    obj.scale = item.get("scale", (1.0, 1.0, 1.0))
    obj.rotation_euler = item.get("rotation", (0.0, 0.0, 0.0))
    obj.data.materials.append(material(item["material"], item["color"]))

for item in SPEC:
    parent_name = item.get("parent")
    if parent_name:
        obj = bpy.data.objects[item["name"]]
        world_matrix = obj.matrix_world.copy()
        obj.parent = bpy.data.objects[parent_name]
        obj.matrix_world = world_matrix

bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
ground = bpy.context.active_object
ground.name = "Bench_Ground"
ground.data.materials.append(material("BenchGround", (0.06, 0.07, 0.09, 1.0)))

bpy.ops.object.light_add(type="AREA", location=(2, -4, 7))
light = bpy.context.active_object
light.name = "Bench_KeyLight"
light.data.energy = 1200
light.data.shape = "DISK"
light.data.size = 5

bpy.ops.object.camera_add(location=(10, -12, 9))
camera = bpy.context.active_object
camera.name = "Bench_Camera"
camera.rotation_euler = ((Vector((0, 0, 1)) - camera.location).to_track_quat("-Z", "Y").to_euler())
bpy.context.scene.camera = camera

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = 640
scene.render.resolution_y = 480
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.world.color = (0.025, 0.03, 0.05)
bpy.ops.wm.save_as_mainfile(filepath='C:\\Users\\caleb\\infinity-code\\backend\\data\\eval_reports\\blender_vehicle_20260802_000525\\vehicle.blend')
