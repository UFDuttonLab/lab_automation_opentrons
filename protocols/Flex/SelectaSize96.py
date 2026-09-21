from opentrons import protocol_api
from opentrons import types
from opentrons.protocol_api import COLUMN, ALL

metadata = {
    'protocolName': 'Select-a-Size DNA Clean & Concentrator Kit',
    'author': 'Tavis Goldwire',
    'description': 'Select-a-Size DNA Clean & Concentrator Kit designed for post PCR clean up.',
    'source': 'Tavis'
}


requirements = {
    'robotType': 'Flex',
    'apiLevel': '2.28'
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
    DryRun = protocol.params.DryRun

# PIPETTES
    pip96 = protocol.load_instrument("flex_96channel_200")


# FIXTURES & MODULES
    waste_chute = protocol.load_waste_chute()
    temp_module = protocol.load_module('temperature module gen2', 'C1')
    temp_adapter = temp_module.load_adapter('opentrons_96_well_aluminum_block')
    mag_block = protocol.load_module('magneticBlockV1', 'D2') 
    # heater_shaker = protocol.load_module('heaterShakerModuleV1', 'D1')


# LABWARE
    transfer_tips_200 = protocol.load_labware('opentrons_flex_96_tiprack_200ul', 'A2') #tips used for prepping reagents

    full_adapter = protocol.load_adapter('opentrons_flex_96_tiprack_adapter', 'B1')
    magbead_tips_200 = full_adapter.load_labware('opentrons_flex_96_tiprack_200ul')

    wash_adapter = protocol.load_adapter('opentrons_flex_96_tiprack_adapter', 'B3')
    wash_tips_50 = wash_adapter.load_labware('opentrons_flex_96_tiprack_50ul')

    elution_adapter = protocol.load_adapter('opentrons_flex_96_tiprack_adapter', 'C3')
    elution_tips_50 = elution_adapter.load_labware('opentrons_flex_96_tiprack_50ul')

    sample_plate = temp_adapter.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt')
    final_elution_plate = protocol.load_labware('opentrons_96_wellplate_200ul_pcr_full_skirt', 'D1')
    reagent_reservoir = protocol.load_labware('nest_12_reservoir_15ml', 'C2')
    waste_reservoir = protocol.load_labware('nest_1_reservoir_195ml', 'B2')

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
    
# FUNCTIONS
    

# START
    protocol.comment('------STARTING PROTOCOL------')
    protocol.comment('STEP 1: Pre-mix magnetic bead reservoir')

    #partial pick up of 8 channel
    pip96.configure_nozzle_layout(style=COLUMN, start="A12", tip_racks=[transfer_tips_200])
    pip96.pick_up_tip(transfer_tips_200.columns()[0][0]) #grab tips with large pipette


    #pip96.mix(repetitions=5, volume=200, location=water_magbead_mix, rate=1.0)
    pip96.mix(repetitions=5, volume=150, location=water_magbead_mix, rate=1.5)
    pip96.blow_out(water_magbead_mix)

    protocol.comment('STEP 2: Dispense Magbeads')

    for col_idx in range(12):
        current_column = sample_columns[col_idx]
        pip96.aspirate(26, water_magbead_mix.bottom(0.3), rate=0.5) #draw up mag bead mix slow
        pip96.dispense(26, current_column[0].top()) #dispense in samples without touching samples
        pip96.blow_out(current_column[0].top(2)) #blow out residue

        if col_idx == 5: #midpoint mix
            pip96.mix(repetitions=5, volume=100, location=water_magbead_mix, rate=1.5)
            pip96.blow_out(water_magbead_mix)

    pip96.drop_tip(waste_chute) #always trash - returning partial-column tips breaks pickup of the next column

    protocol.comment('STEP 3: Mix Samples')

    pip96.configure_nozzle_layout(style=ALL, tip_racks=[magbead_tips_200]) #get full tips
    pip96.pick_up_tip()
    pip96.mix(volume=100, location=sample_plate["A1"], repetitions=10) #mix up the sample with magbeads
    pip96.mix(volume=100, location=sample_plate["A1"], repetitions=10, aspirate_flow_rate=50, dispense_flow_rate=50) #slower mix after
    pip96.blow_out(sample_plate["A1"].top()) #blow out residuals
    pip96.move_to(sample_plate["A1"].top(z=50)) #retract clear of the plate before the delay + gripper move

    # Incubate
    protocol.comment('STEP 4: Incubate samples at room temp')
    protocol.delay(minutes=5 if not DryRun else 0.5)


# STEP 5: Move to magnet
    protocol.comment('STEP 5: Move to magnet')
    protocol.move_labware(sample_plate, mag_block, use_gripper=True)

# STEP 6: Magnetic separation
    protocol.comment('STEP 6: Magnet incubation')
    protocol.delay(minutes=magbead_incubation_time if not DryRun else 0.5)

# STEP 7: Remove supernatant
    protocol.comment('STEP 7: Remove supernatant')

    pip96.aspirate(50, sample_plate['A1'].bottom(0.1), rate=0.5) #draw up mag bead mix slow 
    ######### 26 + sample volume? check this

    pip96.dispense(50, waste.top()) #dispense waste
    pip96.drop_tip(waste_chute) #trash tips



# STEP 8: Add wash buffer
    protocol.comment('STEP 8: Add wash buffer')

    pip96.configure_nozzle_layout(style=COLUMN, start="A12", tip_racks=[transfer_tips_200])
    pip96.pick_up_tip() #grab tips 
    pip96.mix(volume= 100, location=dna_wash_buffer, repetitions= 5, rate=1.5)
    pip96.blow_out(dna_wash_buffer.top())

    for col_idx in range(12):
            current_column = sample_columns[col_idx]
            pip96.aspirate(50, dna_wash_buffer.bottom(0.3), rate=0.5)  # dna_wash_buffer asipiration
            pip96.dispense(50, current_column[0].top()) #dispense in samples without touching samples
            pip96.blow_out(current_column[0].top()) #blow out residue
    
            if col_idx == 5: #midpoint mix
                pip96.mix(repetitions=5, volume=100, location=dna_wash_buffer, rate=1.5)
                pip96.blow_out(dna_wash_buffer)
    pip96.drop_tip()

    protocol.comment('Incubating wash for 30 seconds')
    protocol.delay(minutes=0.5)

# STEP 9: Remove wash
    protocol.comment('STEP 9: Remove wash')

    pip96.configure_nozzle_layout(style=ALL, tip_racks=[wash_tips_50]) #get full tips
    pip96.pick_up_tip()
    pip96.aspirate(50, sample_plate['A1'].bottom(0.1), rate=0.5) #draw up wash buffer slow (first pass)
    pip96.dispense(50, waste.top()) #dispense waste
    pip96.blow_out(waste)
    pip96.aspirate(10, sample_plate['A1'].bottom(0.1), rate=0.5) #second pass to ensure all residual wash buffer is removed
    pip96.dispense(10, waste.top())
    pip96.drop_tip()


# STEP 10: Add elution water
    protocol.comment('STEP 10: Add elution water')

    pip96.configure_nozzle_layout(style=COLUMN, start="A12", tip_racks=[transfer_tips_200])
    pip96.pick_up_tip() #grab tips 

    for col_idx in range(12):
            current_column = sample_columns[col_idx]
            pip96.aspirate(20, dnase_free_water.bottom(0.3), rate=0.5)  # elution aspiration
            pip96.dispense(20, current_column[0].top()) #dispense in samples without touching samples
            pip96.blow_out(current_column[0].top()) #blow out residue
    
            if col_idx == 5: #midpoint mix
                pip96.mix(repetitions=5, volume=100, location=dnase_free_water, rate=1.5)
                pip96.blow_out(dnase_free_water)
    pip96.drop_tip()
    

# STEP 11: Remove from magnet and mix
    protocol.comment('STEP 11: Elution mixing')
    protocol.move_labware(sample_plate, temp_adapter, use_gripper=True)

    pip96.configure_nozzle_layout(style=ALL, tip_racks=[elution_tips_50]) #get full tips
    pip96.pick_up_tip()
    pip96.mix(10, 15, location= sample_plate["A1"]) #match elution add volume so mix doesn't pull air
    pip96.blow_out()
    pip96.return_tip() #put them back will use for removing elution


# STEP 12: Elution incubation
    protocol.comment('STEP 12: Incubate for elution')
    protocol.delay(minutes=5 if not DryRun else 0.5)

# STEP 13: Final magnet separation
    protocol.comment('STEP 13: Final magnet separation')
    protocol.move_labware(sample_plate, mag_block, use_gripper=True)
    protocol.delay(minutes=magbead_incubation_time if not DryRun else 0.5)


# STEP 14: Transfer eluted DNA
    protocol.comment('STEP 14: Transfer eluted DNA')

    pip96.configure_nozzle_layout(style=ALL, tip_racks=[elution_tips_50]) #get full tips
    pip96.pick_up_tip(elution_tips_50['A1'])
    pip96.aspirate(20, location= sample_plate["A1"].bottom(0.5), rate=0.5)
    pip96.dispense(20, location= final_elution_plate["A1"].bottom(0.5))
    pip96.blow_out(final_elution_plate["A1"].top(-0.5))
    pip96.drop_tip()

    protocol.comment('------PROTOCOL COMPLETE------')
