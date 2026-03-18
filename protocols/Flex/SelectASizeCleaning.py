from opentrons import protocol_api
from opentrons import types

metadata = {
    'protocolName': 'Automated Supplemental Protocol - Basic Aliquoting with Magnetic Bead Cleanup',
    'author': 'OpentronsAI, Updated by ChatGPT',
    'description': 'Automated protocol with magnetic bead mixing before each aspirate step.',
    'source': 'OpentronsAI'
}

requirements = {
    'robotType': 'Flex',
    'apiLevel': '2.19'
}

def add_parameters(parameters):
    parameters.add_float(
        display_name="MagBead Incubation (min)",
        variable_name="magbead_incubation_time",
        description="The time in minutes for the magnetic bead incubation step.",
        default=5.0,
        minimum=1.0,
        maximum=30.0
    )
    parameters.add_float(
        display_name="Bead Mixing Volume (µL)",
        variable_name="bead_mixing_volume",
        description="Volume in µL to use for magnetic bead mixing operations.",
        default=200.0,
        minimum=50.0,
        maximum=800.0
    )
    parameters.add_bool(
        display_name="Dry Run",
        variable_name="DryRun",
        description="A dry run will skip all delays and speed up movements.",
        default=False
    )

def run(protocol: protocol_api.ProtocolContext):

    # PARAMETERS
    magbead_incubation_time = protocol.params.magbead_incubation_time
    bead_mixing_volume = protocol.params.bead_mixing_volume
    DryRun = protocol.params.DryRun

    # PIPETTES
    pip50 = protocol.load_instrument("flex_8channel_50", "left")
    pip1000 = protocol.load_instrument("flex_8channel_1000", "right")

    # FLOW RATES
    pip50.flow_rate.aspirate = 20
    pip50.flow_rate.dispense = 20
    pip50.flow_rate.blow_out = 50
    pip1000.flow_rate.aspirate = 50
    pip1000.flow_rate.dispense = 150
    pip1000.flow_rate.blow_out = 300

    # FIXTURES & MODULES
    # Changed from trash bin to waste chute (fixed in slot D3)
    waste_chute = protocol.load_waste_chute()
    temp_module = protocol.load_module('temperature module gen2', 'A3')
    # Use 'opentrons_96_pcr_adapter' (not the older 'opentrons_96_well_aluminum_block').
    # The block definition adds 18.16 mm to the Z-stack vs 13.85 mm for the PCR adapter
    # — a 4.3 mm difference that prevented tips from reaching the well bottom.
    temp_adapter = temp_module.load_adapter('opentrons_96_pcr_adapter')
    mag_block = protocol.load_module('magneticBlockV1', 'C1') 
    heater_shaker = protocol.load_module('heaterShakerModuleV1', 'D1')

    # LABWARE
    mixing_tips_1000 = protocol.load_labware('opentrons_flex_96_tiprack_1000ul', 'A1')
    transfer_tips_50 = protocol.load_labware('opentrons_flex_96_tiprack_50ul', 'A2')
    wash_tips_50 = protocol.load_labware('opentrons_flex_96_tiprack_50ul', 'B1')
    elution_tips_50 = protocol.load_labware('opentrons_flex_96_tiprack_50ul', 'B2')
    sample_plate = temp_adapter.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt')
    final_elution_plate = protocol.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt', 'B3')
    reagent_reservoir = protocol.load_labware('nest_12_reservoir_15ml', 'D2')
    waste_reservoir = protocol.load_labware('nest_12_reservoir_15ml', 'C3')

    # LIQUIDS
    dnase_free_water = reagent_reservoir.wells()[0]
    dna_wash_buffer = reagent_reservoir.wells()[2]
    water_magbead_mix = reagent_reservoir.wells()[11]
    waste = waste_reservoir.wells()[0]

    # Define liquids
    water_liquid = protocol.define_liquid("DNase Free Water", "DNase free water", "#0000FF")
    wash_buffer_liquid = protocol.define_liquid("DNA Wash Buffer", "Wash buffer", "#00FF00")
    water_magbead_liquid = protocol.define_liquid("Water + Magnetic Beads", "Water + beads", "#8B4513")

    dnase_free_water.load_liquid(water_liquid, 3000)
    dna_wash_buffer.load_liquid(wash_buffer_liquid, 6000)
    water_magbead_mix.load_liquid(water_magbead_liquid, 3347.5)

    sample_columns = sample_plate.columns()
    elution_columns = final_elution_plate.columns()

    # FUNCTIONS
    # Updated drop_tip functions to use waste_chute
    def drop_tip_50(): pip50.return_tip() if DryRun else pip50.drop_tip(waste_chute)
    def drop_tip_1000(): pip1000.return_tip() if DryRun else pip1000.drop_tip(waste_chute)

    def bead_mixing(well, pip, mvol, reps=8):
        vol = 200
        pip.flow_rate.aspirate = 500
        pip.flow_rate.dispense = 500
        center = well.top().move(types.Point(0, 0, 5))
        aspbot = well.bottom().move(types.Point(0, 2, 1))
        asptop = well.bottom().move(types.Point(0, -2, 2))
        disbot = well.bottom().move(types.Point(0, 2, 3))
        distop = well.top().move(types.Point(0, 1, -5))
        pip.move_to(center)
        for i in range(reps):
            pip.aspirate(vol, aspbot)
            pip.dispense(vol, distop)
            pip.aspirate(vol, asptop)
            pip.dispense(vol, disbot)
        pip.flow_rate.aspirate = 50
        pip.flow_rate.dispense = 150

    def mixing(well, pip, mvol, reps=8):
        center = well.top(5)
        asp = well.bottom(1)
        disp = well.top(-8)
        vol = min(mvol, 1000) * 0.8
        pip.flow_rate.aspirate = 500
        pip.flow_rate.dispense = 500
        pip.move_to(center)
        for i in range(reps):
            pip.aspirate(vol, asp)
            pip.dispense(vol, disp)
        pip.flow_rate.aspirate = 10 if pip == pip50 else 50
        pip.flow_rate.dispense = 20 if pip == pip50 else 150

    # START
    protocol.comment('------STARTING PROTOCOL------')
    protocol.comment('STEP 1: Pre-mix magnetic bead reservoir')
    pip1000.pick_up_tip(mixing_tips_1000.columns()[0][0])
    
    # STEP 2: Transfer magbeads (mix before each aspirate)
    protocol.comment('STEP 2: Transfer magbeads with 50µL tips (with mixing before each)')
    pip50.pick_up_tip(transfer_tips_50.columns()[0][0])
    for col_idx in range(12):
        current_column = sample_columns[col_idx]
        bead_mixing(water_magbead_mix, pip1000, 200 - (10 * col_idx), 5)
        pip50.aspirate(26, water_magbead_mix.bottom(0.3))
        pip50.dispense(26, current_column[0].bottom(0.3))
        pip50.return_tip()
        if col_idx < 11:
            pip50.pick_up_tip(transfer_tips_50.columns()[col_idx + 1][0])
    pip1000.return_tip()

    # STEP 4: Final mix with pip50
    protocol.comment('STEP 4: Final mix with pip50')
    for col_idx in range(12):
        pip50.pick_up_tip(transfer_tips_50.columns()[col_idx][0])
        mixing(sample_columns[col_idx][0], pip50, 20, 20)
        pip50.return_tip()

    # STEP 5: Incubate
    protocol.comment('STEP 5: Incubate samples at room temp')
    protocol.delay(minutes=5 if not DryRun else 0.5)

    # STEP 6: Move to magnet
    protocol.comment('STEP 6: Move to magnet')
    protocol.move_labware(sample_plate, mag_block, use_gripper=True)

    # STEP 7: Magnetic separation
    protocol.comment('STEP 7: Magnet incubation')
    protocol.delay(minutes=magbead_incubation_time if not DryRun else 0.5)

    # STEP 8: Remove supernatant
    protocol.comment('STEP 8: Remove supernatant')
    for col_idx in range(12):
        pip50.pick_up_tip(transfer_tips_50.columns()[col_idx][0])
        pip50.aspirate(35, sample_columns[col_idx][0].bottom(0.3), rate=0.75)
        pip50.dispense(35, waste.bottom(1))
        pip50.drop_tip()

    # STEP 9: Add wash buffer
    protocol.comment('STEP 9: Add wash buffer')
    pip1000.pick_up_tip(mixing_tips_1000.columns()[1][0])
    mixing(dna_wash_buffer, pip1000, bead_mixing_volume, 5)
    pip1000.return_tip()

    for col_idx in range(12):
        pip50.pick_up_tip(wash_tips_50.columns()[col_idx][0])
        pip50.aspirate(50, dna_wash_buffer.bottom(0.3))
        pip50.dispense(50, sample_columns[col_idx][0].bottom(0.5))
        pip50.blow_out(sample_columns[col_idx][0].top(-2))
        pip50.return_tip()

    # STEP 10: Remove wash
    protocol.comment('STEP 10: Remove wash')
    
    for col_idx in range(12):
        pip50.pick_up_tip(wash_tips_50.columns()[col_idx][0])
        pip50.aspirate(50, sample_columns[col_idx][0].bottom(0.3), rate=0.10)
        pip50.dispense(50, waste.bottom(1))
        pip50.drop_tip()

    if not DryRun:
        protocol.pause("Ensure all buffer removed.")
        
    # STEP 11: Add elution water
    protocol.comment('STEP 11: Add elution water')
    pip1000.pick_up_tip(mixing_tips_1000.columns()[3][0])
    mixing(dnase_free_water, pip1000, bead_mixing_volume, 5)
    pip1000.return_tip()

    for col_idx in range(12):
        pip50.pick_up_tip(elution_tips_50.columns()[col_idx][0])
        pip50.aspirate(20, dnase_free_water.bottom(0.2))
        pip50.dispense(20, sample_columns[col_idx][0].bottom(0.2))
        pip50.return_tip()

    # STEP 12: Remove from magnet and mix
    protocol.comment('STEP 12: Elution mixing')
    protocol.move_labware(sample_plate, temp_adapter, use_gripper=True)
    for col_idx in range(12):
        pip50.pick_up_tip(elution_tips_50.columns()[col_idx][0])
        mixing(sample_columns[col_idx][0], pip50, 18, 20)
        pip50.return_tip()

    # STEP 13: Elution incubation
    protocol.comment('STEP 13: Incubate for elution')
    protocol.delay(minutes=5 if not DryRun else 0.5)

    # STEP 14: Final magnet separation
    protocol.comment('STEP 14: Final magnet separation')
    protocol.move_labware(sample_plate, mag_block, use_gripper=True)
    protocol.delay(minutes=magbead_incubation_time if not DryRun else 0.5)

    # STEP 15: Transfer eluted DNA
    protocol.comment('STEP 15: Transfer eluted DNA')
    for col_idx in range(12):
        pip50.pick_up_tip(elution_tips_50.columns()[col_idx][0])
        pip50.aspirate(18, sample_columns[col_idx][0].bottom(0.2), rate=0.1)
        pip50.dispense(18, elution_columns[col_idx][0].bottom(0.5))
        pip50.blow_out(elution_columns[col_idx][0].top(-2))
        pip50.drop_tip()

    protocol.comment('------PROTOCOL COMPLETE------')
