
AHProfitDB = {
	["addonVersion"] = "0.1.0",
	["characters"] = {
		["Amaya-Grommash"] = {
			["class"] = "MAGE",
			["faction"] = "Horde",
			["gold"] = 1234567,
			["level"] = 60,
			["name"] = "Amaya",
			["realm"] = "Grommash",
			["updated"] = 1760000000000,
		},
		["Brugan-Grommash"] = {
			["class"] = "WARRIOR",
			["faction"] = "Horde",
			["gold"] = 456789,
			["level"] = 40,
			["name"] = "Brugan",
			["realm"] = "Grommash",
			["updated"] = 1760003600000,
		},
	},
	["events"] = {
		{
			["at"] = 1760003660000,
			["char"] = "Brugan-Grommash",
			["confirmed"] = true,
			["deposit"] = 60,
			["durationH"] = 24,
			["id"] = "Brugan-Grommash:1760003660000:0",
			["key"] = 6217,
			["price"] = 4100,
			["stack"] = 5,
			["type"] = "post",
		}, -- [1]
	},
	["formatVersion"] = 1,
	["inventory"] = {
		["Amaya-Grommash"] = {
			["bags"] = "-6585,1,0;2589,20,0;6948,1,1;10938,2,0;10940,3,0",
			["bagsAt"] = 1760000000000,
			["bank"] = "2589,7,0;4306,10,0",
			["bankAt"] = 1760000060000,
			["mail"] = "2592,20,0;10940,3,0",
			["mailAt"] = 1760000120000,
		},
		["Brugan-Grommash"] = {
			["bags"] = "2835,12,0;2840,6,0;6217,5,0;6948,1,1",
			["bagsAt"] = 1760003600000,
			["bank"] = "",
			["bankAt"] = 1760003610000,
		},
	},
	["itemNames"] = {
		[-6585] = "1,Scouting Gloves,0,4",
		[2589] = "1,Linen Cloth,3,7",
		[2592] = "1,Wool Cloth,0,7",
		[2835] = "1,Rough Stone,0,7",
		[2840] = "1,Copper Bar,0,7",
		[4306] = "1,Silk Cloth,0,7",
		[6217] = "1,Copper Rod,0,7",
		[6948] = "1,Hearthstone,0,7",
		[10938] = "1,Lesser Magic Essence,0,7",
		[10940] = "1,Strange Dust,0,7",
	},
	["knownRecipes"] = {
		["Amaya-Grommash"] = "7418;7421",
		["Brugan-Grommash"] = "2660;2662;2663",
	},
	["mailSeen"] = {
	},
	["ownAuctions"] = {
	},
	["professions"] = {
		["Amaya-Grommash"] = {
			["Cooking"] = {
				["max"] = 75,
				["skill"] = 45,
			},
			["Enchanting"] = {
				["max"] = 150,
				["skill"] = 142,
			},
		},
		["Brugan-Grommash"] = {
			["Blacksmithing"] = {
				["max"] = 150,
				["skill"] = 115,
			},
			["Mining"] = {
				["max"] = 150,
				["skill"] = 120,
			},
		},
	},
	["recipes"] = {
		["Blacksmithing"] = {
			[2660] = {
				["product"] = 2862,
				["reagents"] = "2835,1",
				["yieldMax"] = 1,
				["yieldMin"] = 1,
			},
			[2662] = {
				["product"] = 2852,
				["reagents"] = "2840,4",
				["yieldMax"] = 1,
				["yieldMin"] = 1,
			},
			[2663] = {
				["product"] = 6217,
				["reagents"] = "2840,2",
				["yieldMax"] = 1,
				["yieldMin"] = 1,
			},
		},
		["Enchanting"] = {
			[7418] = {
				["reagents"] = "10940,1",
				["yieldMax"] = 1,
				["yieldMin"] = 1,
			},
			[7421] = {
				["product"] = 6218,
				["reagents"] = "6217,1;10938,1;10940,1",
				["yieldMax"] = 1,
				["yieldMin"] = 1,
			},
		},
	},
	["reserveTimes"] = {
	},
	["reserves"] = {
	},
	["rules"] = {
	},
	["scans"] = {
	},
	["vendorReagents"] = "2320;2321",
}
