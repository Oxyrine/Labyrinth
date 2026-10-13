.PHONY: world pipeline report demo test guards

world:
	python run.py world --regime A

pipeline:
	python run.py pipeline

report:
	python run.py report

demo:
	TESSERA_DEMO=1 python run.py demo

test:
	python run.py test

guards:
	python run.py guards
