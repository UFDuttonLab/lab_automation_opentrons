from opentrons import protocol_api
from opentrons.protocol_api import COLUMN, ALL
import math
from opentrons import types

metadata = {
    'protocolName': 'Zymo Quick-16S™ Plus NGS Library Prep Kit (V1-V3) D6440 - V2.4 - AM (96ch 200uL Head)', # modified for new flex_96channel_200 pipette
    'author': 'Opentrons <protocols@opentrons.com>',
}

requirements = {
    'robotType': 'Flex',
    'apiLevel': '2.19'
}

def add_parameters(parameters):
    parameters.add_bool(
        display_name="Dry Run",
        variable_name="DryRun",
        description="Dry runs will skip all Thermocycler & Temperature Module programs",
        default=False
        )

    parameters.add_bool(
        display_name="Using Thermocycler",
        variable_name="using_thermocycler",
        description="If yes, skip pause step before moving plate to thermocycler",
        default=True
        )

def run(protocol: protocol_api.ProtocolContext):

    # PARAMETERS
    DryRun = protocol.params.DryRun
    using_thermocycler = protocol.params.using_thermocycler

# PIPETTE
    # Switched from flex_96channel_1000 (200-1000uL range) to the newer
    # flex_96channel_200 (1-200uL range). All volumes used in this protocol
    # (2 uL DNA, 9 uL premix, 6.5 uL pooling, 7 uL mix) are well within the
    # 1-200 uL working range of this pipette.
    # NOTE: The 200 uL 96-head is NOT compatible with 1000 uL tips. Only
    # 50 uL and 200 uL Flex tip racks should be used with this pipette.
    pip96 = protocol.load_instrument(
        instrument_name="flex_96channel_200",
        mount="left")

    # pip96.flow_rate.aspirate = 50
    # pip96.flow_rate.dispense = 150
    # pip96.flow_rate.blow_out = 300

    # Default flow rates for the 96-ch 200 uL head:
    # Tip50  - 6 uL/s
    # Tip200 - 80 uL/s
    # (Tip1000 is not supported on this pipette)
    # well bottom clearance of 1 mm for aspirate and dispense actions

    chute = protocol.load_waste_chute()

# DECK 1
    full_adapter = protocol.load_adapter('opentrons_flex_96_tiprack_adapter', location='A3')
    # tip1000_full1 = full_adapter.load_labware('opentrons_flex_96_tiprack_1000ul') # NOT compatible with flex_96channel_200 - foil-pierce step would need a 200 uL tiprack instead
    tip50_full1 = full_adapter.load_labware('opentrons_flex_96_tiprack_50ul') # this is for aspirating 8uL premix
    tip50_full2 = protocol.load_labware('opentrons_flex_96_tiprack_50ul', 'A4') # this is for aspirating 2uL DNA
    tip50_partial1 = protocol.load_labware('opentrons_flex_96_tiprack_50ul', 'D1') # this is for pooling 5uL

# LOAD MODULES
    temp_module = protocol.load_module('temperature module gen2', 'C1')
    # Use 'opentrons_96_pcr_adapter' (not the older 'opentrons_96_well_aluminum_block').
    # The block definition adds 18.16 mm to the Z-stack vs 13.85 mm for the PCR adapter
    # — a 4.3 mm difference that prevented tips from reaching the well bottom.
    temp_adapter = temp_module.load_adapter('opentrons_96_pcr_adapter')
    tc_mod = protocol.load_module(module_name="thermocyclerModuleV2")
    tc_mod.open_lid()
    mag_block = protocol.load_module('magneticBlockV1', 'D2')
    chute = protocol.load_waste_chute()

# LOAD LABWARE
    sample_plate = protocol.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt', 'C2') # fix location
    premix_plate = protocol.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt', 'B2') # fix location
    dna_sample_plate = protocol.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt', 'B3') # fix location
    pooled_plate = protocol.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt', 'C3') # fix location

    samples = sample_plate.wells()[0]
    premixes = premix_plate.wells()[0]
    dna_samples = dna_sample_plate.wells()[0]

    pools_samples = sample_plate.rows()[0][:12]
    final_plate = pooled_plate.rows()[0][0]

# REAGENTS
    premix_vol = 9
    dna_vol = 2
    pool_vol = 6.5

    def drop():
        if DryRun:
            pip96.return_tip()
        else:
            pip96.drop_tip(chute)

    def move_chute(labware):
        if DryRun:
            protocol.move_labware(labware, chute, use_gripper=False)
        else:
            protocol.move_labware(labware, chute, use_gripper=True)

    def move_gripper(labware, loc):
        protocol.move_labware(labware=labware, new_location=loc, use_gripper=True)

    def move_manually(labware, loc):
        protocol.move_labware(labware=labware, new_location=loc, use_gripper=False)

    def mix(mix,volume,labware):
        for x in range (mix):
            pip96.aspirate(volume,labware,rate=0.03) # 0.2 rate is 32uL/sec
            pip96.dispense(volume,labware,rate=0.03)

## START PROTOCOL
    protocol.comment('------SECTION 1:1-STEP PCR-----')

# Turn Off Temp Mod
    temp_module.deactivate()
    if not DryRun and using_thermocycler:
        tc_mod.set_lid_temperature(temperature=105)

# # 1 - Pierce the foil
#   NOTE: Original foil-pierce step used a 1000 uL tiprack. The flex_96channel_200
#   pipette cannot use 1000 uL tips. If foil piercing is needed, swap the tip1000
#   rack for a 200 uL tiprack (opentrons_flex_96_tiprack_200ul) and re-test reach/depth.
#     pip96.configure_nozzle_layout(style=ALL,start="A1")
#     pip96.pick_up_tip(tip1000_full1)
#     well = premixes
#     pip96.move_to(well.top(-2)) # Pierce a small hole at the centre 2 mm deep inside the plate
#     pip96.move_to(well.bottom().move(types.Point(x=-1.5,y=1)),force_direct = True,speed=10) # move the pipette 1.5 mm in x and 1 mm in y from the center to the bottom
#     pip96.return_tip()

#     # Move tip rack
#     move_gripper(tip1000_full1,'C4')
#     move_gripper(tip50_full1,full_adapter)

# Transfer DNA and Master Mix
    pip96.configure_nozzle_layout(style=ALL,start="A1")
    pip96.pick_up_tip(tip50_full1)
    pip96.aspirate(dna_vol,dna_samples.bottom(2),rate=0.03)
    pip96.aspirate(premix_vol,premixes.bottom(2),rate=0.03) # aspirate the desired volume + 1uL for reverse dispensing.
    protocol.delay(seconds=1) #added 1 sec delay to allow for liquid to settle after aspiration before tip removal from the liquid
    pip96.dispense(dna_vol+premix_vol,samples.bottom(2),push_out=0,rate=0.05)
    for x in range(2):
        pip96.aspirate(7,samples.bottom(2),rate=0.05)
        pip96.dispense(7,samples.bottom(2),push_out=0,rate=0.05)
    pip96.aspirate(7,samples.bottom(2),rate=0.05)
    pip96.dispense(7,samples.bottom(3),push_out=1.8,rate=0.025)
    protocol.delay(seconds=1) #added 1 sec delay to allow for liquid to settle after mixing before tip removal from the liquid
    drop()

    # move_chute(tip50_full2)

# 5 - Thermal Cycler Program or Manual Processing
    if using_thermocycler:
        # Using thermocycler workflow
        tc_mod.open_lid()

        move_gripper(sample_plate,tc_mod)
        tc_mod.close_lid()

        # Wrap thermocycler operations in dry run condition
        if not DryRun:
            profile1 = [
                {"temperature":95, "hold_time_seconds":600}
            ]
            tc_mod.execute_profile(steps=profile1, repetitions=1)
            profile2 = [
            {"temperature":95, "hold_time_seconds":30},
            {"temperature":55, "hold_time_seconds":30},
            {"temperature":72, "hold_time_seconds":180}
        ]
            tc_mod.execute_profile(steps=profile2, repetitions=42)
            tc_mod.set_block_temperature(temperature=4)

        tc_mod.open_lid()
        if not DryRun:
            tc_mod.deactivate_lid()
    else:
        # Not using thermocycler workflow
        protocol.pause("Please remove the sample plate and place it in an external thermocycler or incubator for PCR amplification. After PCR is complete, place the plate back in slot C2 and resume the protocol.")

    protocol.comment('------SECTION 2:POOLING BY EQUAL VOL-----')

    # Pooling into a single column

    pools_samples = sample_plate.rows()[0][:12]
    final_plate = pooled_plate.rows()[0][0]

    pip96.configure_nozzle_layout(style=COLUMN, start="A1", tip_racks=[tip50_partial1])
    pip96.pick_up_tip()
    for well in pools_samples:
        pip96.aspirate(pool_vol,well.bottom(1),rate=0.025)
        protocol.delay(seconds=1)
        pip96.dispense(pool_vol,final_plate.bottom(2),push_out=4,rate=0.015)
        pip96.blow_out(final_plate.top(-1))
    pip96.drop_tip()

    protocol.comment('------PROTOCOL IS COMPLETE------')
