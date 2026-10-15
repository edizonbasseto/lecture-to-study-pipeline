// Slide layout authored for this repository (MIT license). It renders
// script.yaml, the single source of the demo lecture, and uses no
// third-party assets, fonts or images.
#let lecture = yaml("script.yaml")

#set page(width: 1920pt, height: 1080pt, margin: 140pt)
#set text(lang: "pt", size: 44pt)

#for slide in lecture.slides [
  #page(fill: rgb(slide.background))[
    #set text(fill: rgb(slide.foreground))
    #align(horizon)[
      #text(size: 96pt, weight: "bold")[#slide.heading]
      #v(60pt)
      #for bullet in slide.bullets [
        - #bullet
      ]
    ]
  ]
]
