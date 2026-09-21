'''
Draft protocol for the ZymoBIOMICS 96 DNA kit on an Opentrons Flex with a Vacuum Module

The user will perform the cell lysis transfer the clarified lysate into a 2mL 96-well block. 
The Opentrons Flex will perform the following steps:
1. Add 1.2mL of binding buffer to each well containing 400µl of clarified lysate and mix well
2. Transfer 800µl of the lysate + binding buffer into the Zymo-Spin I-96-Z Plate and vacuum
3. Transfer the rest 800µl of the lysate + binding buffer into the Zymo-Spin I-96-Z Plate and vacuum
4. Dispense 400µl of DNA Wash Buffer 1 to each well and vacuum
5. Dispense 700µl of DNA Wash Buffer 2 to each well and vacuum
6. Dispense 200µl of DNA Wash Buffer 2 to each well and vacuum
7. Vacuum empty Zymo-Spin I-96-Z Plate to dry membranes
8. Place the Zymo-Spin I-96-Z Plate on top the Elution Plate and both inside the Opentrons tall collar
9. Dispense 50µl of ZymoBIOMICS™ DNase/RNase Free Water on each well, wait 1', and vacuum
'''


from opentrons import protocol_api, types
from opentrons.protocol_api import SINGLE, ALL, PARTIAL_COLUMN, COLUMN
from typing import cast
from opentrons.protocol_api import (
    ProtocolContext,
    ParameterContext,
    VacuumModuleContext
)
from math import ceil, floor


metadata = {
    'protocolName': 'ZymoBIOMICS 96 DNA kit on an Opentrons Flex with a Vacuum Module',
    'author': 'Opentrons Inc.',
    'description': 'Miniprep protocol with 96-channel pipette and vacuum manifold integration',
    'source': 'Opentrons Inc.'
}

requirements = {
    'robotType': 'Flex',
    'apiLevel': '2.30'
}

def add_parameters(parameters):

    parameters.add_str(
        variable_name="collar",
        display_name="Vacuum Collar",
        description="The kind of Collar (Opentrons Tall or Opentrons Short)",
        default="opentrons_vacuum_manifold_collar_tall",
        choices=[
            {
                "display_name": "Opentrons Short",
                "value": "opentrons_vacuum_manifold_collar_short",
            },
            {
                "display_name": "Opentrons Tall",
                "value": "opentrons_vacuum_manifold_collar_tall",
            },            
        ],
    )


def vacuum(ctx, vm_mod, pressure, time, vent=True, equalize_time=20):
        '''
        Function to operate the vacuum manifold. 
        pressure: vacuum pressure in mbars (from 0 to -800)
        time: duration of the vacuum in seconds
        vent: variable to indicate wether to open the vent (true) or not (false) after vacuuming
        equilize_time: time in seconds to wait after vacuuming to let the pressure equalize \
            before moving to the next step
        '''
        vm_mod.close_vent()
        task1 = vm_mod.start_set_vacuum_pressure(pressure, time, vent_after=vent, equalize_timeout_s=equalize_time)
        ctx.wait_for_tasks([task1])


def run(ctx: protocol_api.ProtocolContext):
    # LOAD MODULES

    waste_chute = ctx.load_waste_chute()

    vm_mod = cast( 
            VacuumModuleContext,
            ctx.load_module(module_name="vacuumModuleV1", location="A3"))
    # Vacuum module loaded indicating the vacuum manifold in deck position A3

    manifold_collar = vm_mod.load_adapter(ctx.params.collar)
    # Manifold collar loaded on the vacuum manifold


    # LOAD LABWARE

    sample_plate = ctx.load_labware("nest_96_wellplate_2ml_deep", 'A2', 'Sample plate with clarified lysates')
    # Sample plate containing 400µl of clarified lysates 

    zymo_spin_plate = manifold_collar.load_labware("thermoscientificnunc_96_wellplate_1000ul_filter", 
                                                     "Zymo-Spin I-96-Z Plate")
    # Zymo-Spin I-96-Z Plate loaded on the manifold collar on top of the vacuum manifold in A3
    # In this protocol we are using the thermoscientificnunc_96_wellplate_1000ul_filter labware as an example
    # filter plate labware but to run the actual ZymoBIOMICS kit a new custom labware will need to be created
    # with the specific measurements of the Zymo-Spin I-96-Z Plate

    #elution_plate = ctx.load_labware("eppendorf_96_wellplate_150ul", "D2") #,  "Elution Plate"
    # In this protocol we are using a standard PCR plate as elution plate but to run the actual ZymoBIOMICS kit 
    # a new custom labware will need to be created with the specific measurements of the kit's Elution Plate. In
    # this example we are not using any spacer

    tall_spacer = ctx.load_adapter("opentrons_vacuum_manifold_spacer_tall", "D2") #, "Opentrons Vacuum Tall Spacer" switched to load_adapter TG
    elution_plate = tall_spacer.load_labware("eppendorf_96_wellplate_150ul") #, 'Elution plate'
    # If using a spacer to increase the height of a plate inside the vacuum collar, the spacer needs to be loaded
    # as a module and the plate needs to be loaded on top of the spacer. In this example, we are loading the tall
    # spacer on slot D2 and the elution plate on top of the spacer

    binding_buffer_reservoir = ctx.load_labware("nest_1_reservoir_195ml", "B2")
    # 1-well reservoir for the binding buffer

    wash1_buffer_reservoir = ctx.load_labware("nest_1_reservoir_195ml", "B3")
    # 1-well reservoir for the DNA Wash Buffer 1

    wash2_buffer_reservoir = ctx.load_labware("nest_1_reservoir_195ml", "C2")
    # 1-well reservoir for the DNA Wash Buffer 2

    elution_buffer_reservoir = ctx.load_labware("nest_1_reservoir_195ml", "C3")
    # 1-well reservoir for the DNase/RNase Free Water

    tiprack_adapter_1 = ctx.load_adapter('opentrons_flex_96_tiprack_adapter', "A1")
    tips_1000_1 = tiprack_adapter_1.load_labware("opentrons_flex_96_tiprack_1000ul", "A1")
    tiprack_adapter_2 = ctx.load_adapter('opentrons_flex_96_tiprack_adapter', "B1")
    tips_1000_2 = tiprack_adapter_2.load_labware("opentrons_flex_96_tiprack_1000ul", "B1")
    tiprack_adapter_3 = ctx.load_adapter('opentrons_flex_96_tiprack_adapter', "C1")
    tips_1000_3 = tiprack_adapter_3.load_labware("opentrons_flex_96_tiprack_1000ul", "C1")
    tiprack_adapter_4 = ctx.load_adapter('opentrons_flex_96_tiprack_adapter', "D1")
    tips_1000_4 = tiprack_adapter_4.load_labware("opentrons_flex_96_tiprack_1000ul", "D1")

    tip_racks = [tips_1000_1, tips_1000_2, tips_1000_3, tips_1000_4]


    # LOAD PIPETTE

    pip_96_1000 = ctx.load_instrument(
        'flex_96channel_1000',
        'left',
        tip_racks=tip_racks
    )


    # LOAD LIQUIDS

    binding_buffer = ctx.define_liquid(
            name="Binding Buffer",
            description="DNA binding buffer",
            display_color="#FF0000"
        )

    wash1_buffer = ctx.define_liquid(
            name="DNA Wash Buffer 1",
            description="DNA Wash Buffer 1",
            display_color="#FFFB00"
        )

    wash2_buffer = ctx.define_liquid(
            name="DNA Wash Buffer 2",
            description="DNA Wash Buffer 2",
            display_color="#00FF6E"
        )

    elution_buffer = ctx.define_liquid(
            name="DNase/RNase Free Water",
            description="DNase/RNase Free Water",
            display_color="#0048FF"
        )

    binding_buffer_volume = 1200 * 96
    wash1_buffer_volume = 400 * 96
    wash2_buffer_volume = 900 * 96
    elution_buffer_volume = 50 * 96
    reservoir_dead_volume = 10000

    binding_buffer_reservoir['A1'].load_liquid(liquid=binding_buffer, 
        volume=binding_buffer_volume+reservoir_dead_volume)
    
    wash1_buffer_reservoir['A1'].load_liquid(liquid=wash1_buffer, 
        volume=wash1_buffer_volume+reservoir_dead_volume)
    
    wash2_buffer_reservoir['A1'].load_liquid(liquid=wash2_buffer, 
        volume=wash2_buffer_volume+reservoir_dead_volume)
    
    elution_buffer_reservoir['A1'].load_liquid(liquid=elution_buffer, 
        volume=elution_buffer_volume+reservoir_dead_volume)


    # PROTOCOL STEPS
    
#### MIX BINDING BUFFER WITH LYSATE AND LOAD INTO FILTER PLATE ##################

    pip_96_1000.pick_up_tip()
    pip_96_1000.aspirate(600, binding_buffer_reservoir['A1'].bottom())
    pip_96_1000.dispense(600, sample_plate['A1'].top(z=-5))
    pip_96_1000.aspirate(600, binding_buffer_reservoir['A1'].bottom())
    pip_96_1000.dispense(600, sample_plate['A1'].top(z=-5))
    for mix in range(6):
         pip_96_1000.aspirate(900, sample_plate['A1'].bottom(z=5), rate=2)
         pip_96_1000.dispense(900, sample_plate['A1'].bottom(z=5), rate=2)

    for load in range(2):
        pip_96_1000.aspirate(850, sample_plate['A1'].bottom()) #flagged might be too high would reduce down to 800
        pip_96_1000.dispense(850, zymo_spin_plate['A1'].top(z=-5))
        vacuum(ctx, vm_mod, -500, 60) # Vacuum at -500mbar for 60 seconds

    pip_96_1000.drop_tip()

#### WASH WITH DNA WASH BUFFER 1 ##################

    pip_96_1000.pick_up_tip()
    pip_96_1000.aspirate(400, wash1_buffer_reservoir['A1'].bottom())
    pip_96_1000.dispense(400, zymo_spin_plate['A1'].top(z=-5))
    vacuum(ctx, vm_mod, -500, 60) # Vacuum at -500mbar for 60 seconds
    pip_96_1000.drop_tip()


#### WASH WITH DNA WASH BUFFER 2 AND DRY MEMBRANES ##################

    pip_96_1000.pick_up_tip()
    pip_96_1000.aspirate(700, wash2_buffer_reservoir['A1'].bottom())
    pip_96_1000.dispense(700, zymo_spin_plate['A1'].top(z=-5))
    vacuum(ctx, vm_mod, -500, 60) # Vacuum at -500mbar for 60 seconds
    pip_96_1000.aspirate(200, wash2_buffer_reservoir['A1'].bottom())
    pip_96_1000.dispense(200, zymo_spin_plate['A1'].top(z=-5))
    vacuum(ctx, vm_mod, -500, 60)  # Vacuum at -500mbar for 60 seconds
    pip_96_1000.drop_tip()
    vacuum(ctx, vm_mod, -800, 300)  # Vacuum at -800mbar for 5 minutes to dry the membranes

#### ELUTION ##################

    ctx.move_labware(manifold_collar, vm_mod.manifold_dock, use_gripper=True) # Move the collar with the filter plate to the dock at A4

    #ctx.move_labware(elution_plate, vm_mod, use_gripper=True) # Move the elution plate on the vacuum manifold
    # This applies if the elution plate is not resting on top of a spacer

    ctx.move_labware(tall_spacer, vm_mod, use_gripper=True) # Move the tall spacer + elution plate on the vacuum manifold
    # This applies if the elution plate is resting on top of a spacer and we want to move both

    ctx.move_labware(zymo_spin_plate, elution_plate, use_gripper=True) # Move the filter plate on top of the elution plate
    ctx.move_labware(manifold_collar, vm_mod, use_gripper=True) # Place the collar on the vacuum manifold with the elution plate + filter plate inside

    pip_96_1000.pick_up_tip()
    pip_96_1000.aspirate(50, elution_buffer_reservoir['A1'].bottom())
    pip_96_1000.dispense(50, zymo_spin_plate['A1'].bottom(z=10))
    pip_96_1000.drop_tip()
    ctx.delay(seconds=60)
    vacuum(ctx, vm_mod, -500, 60) # Vacuum at -500mbar for 60 seconds


